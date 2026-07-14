from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.models import Category, ObjectCleanupJob, Product, ProductSku
from app.schemas.catalog import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    ProductCreate,
    ProductImageRead,
    ProductRead,
    ProductSkuInput,
    ProductSkuRead,
    ProductSpecification,
    ProductUpdate,
    _validate_sku_attribute_references,
)
from app.services.object_cleanup import enqueue_object_cleanup


class CatalogService:
    def list_categories(self, session: Session, *, active_only: bool = False) -> list[Category]:
        query = select(Category).order_by(Category.sort_order, Category.name, Category.id)
        if active_only:
            query = query.where(Category.is_active.is_(True))
        return list(session.scalars(query))

    def create_category(self, session: Session, payload: CategoryCreate) -> Category:
        self._ensure_category_parent(session, payload.parent_id)
        category = Category(**payload.model_dump())
        session.add(category)
        self._commit(session, "category_code_conflict", "Category code already exists")
        session.refresh(category)
        return category

    def update_category(
        self,
        session: Session,
        category_id: int,
        payload: CategoryUpdate,
    ) -> Category:
        # Parent changes are rare and the category set is small. A deterministic
        # table-wide row lock prevents two concurrent updates from creating A↔B cycles.
        list(session.scalars(select(Category.id).order_by(Category.id).with_for_update()))
        category = self.get_category(session, category_id)
        values = payload.model_dump(exclude_unset=True)
        if "code" in values and values["code"] != category.code:
            raise ApiError(
                422,
                "immutable_category_code",
                "Category code is a stable identifier and cannot be changed",
            )
        if values.get("parent_id") == category_id:
            raise ApiError(422, "invalid_category_parent", "A category cannot be its own parent")
        if "parent_id" in values:
            self._ensure_category_parent(session, values["parent_id"])
            self._ensure_no_category_cycle(session, category, values["parent_id"])
        for field, value in values.items():
            setattr(category, field, value)
        self._commit(session, "category_code_conflict", "Category code already exists")
        session.refresh(category)
        return category

    def delete_category(self, session: Session, category_id: int) -> None:
        category = self.get_category(session, category_id)
        child_exists = session.scalar(
            select(Category.id).where(Category.parent_id == category_id).limit(1)
        )
        product_exists = session.scalar(
            select(Product.id).where(Product.category_id == category_id).limit(1)
        )
        if child_exists is not None or product_exists is not None:
            raise ApiError(
                409,
                "category_in_use",
                "Category cannot be deleted while it has child categories or products",
            )
        session.delete(category)
        session.commit()

    @staticmethod
    def get_category(session: Session, category_id: int) -> Category:
        category = session.get(Category, category_id)
        if category is None:
            raise ApiError(404, "category_not_found", "Category was not found")
        return category

    def list_products(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        query_text: str | None = None,
        category_id: int | None = None,
        category_code: str | None = None,
        status: str | None = None,
        public_only: bool = False,
    ) -> tuple[list[Product], int]:
        filters = []
        if query_text:
            search = f"%{query_text.strip()}%"
            filters.append(or_(Product.name.ilike(search), Product.product_code.ilike(search)))
        if category_id is not None:
            filters.append(Product.category_id == category_id)
        if category_code:
            filters.append(Category.code == category_code.upper())
        if status:
            filters.append(Product.status == status)
        if public_only:
            filters.extend([Product.status == "published", Category.is_active.is_(True)])

        base = select(Product).join(Product.category).where(*filters)
        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        statement = (
            base.options(
                selectinload(Product.category),
                selectinload(Product.skus),
                selectinload(Product.images),
            )
            .order_by(Product.featured.desc(), Product.sort_order, Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(session.scalars(statement)), total

    def get_product(
        self,
        session: Session,
        product_id: int,
        *,
        public_only: bool = False,
    ) -> Product:
        statement = (
            select(Product)
            .join(Product.category)
            .options(
                selectinload(Product.category),
                selectinload(Product.skus),
                selectinload(Product.images),
            )
            .where(Product.id == product_id)
        )
        if public_only:
            statement = statement.where(
                Product.status == "published",
                Category.is_active.is_(True),
            )
        product = session.scalar(statement)
        if product is None:
            raise ApiError(404, "product_not_found", "Product was not found")
        return product

    def get_product_by_code(
        self,
        session: Session,
        product_code: str,
        *,
        public_only: bool = False,
    ) -> Product:
        statement = (
            select(Product)
            .join(Product.category)
            .options(
                selectinload(Product.category),
                selectinload(Product.skus),
                selectinload(Product.images),
            )
            .where(Product.product_code == product_code.upper())
        )
        if public_only:
            statement = statement.where(
                Product.status == "published",
                Category.is_active.is_(True),
            )
        product = session.scalar(statement)
        if product is None:
            raise ApiError(404, "product_not_found", "Product was not found")
        return product

    def create_product(self, session: Session, payload: ProductCreate) -> Product:
        category = self.get_category(session, payload.category_id)
        product_values = payload.model_dump(exclude={"skus"})
        product = Product(**product_values)
        product.category = category
        session.add(product)
        self._sync_skus(session, product, payload.skus)
        self._validate_product(product)
        if product.status == "published":
            self._validate_publish(product)
        self._commit(session, "product_code_conflict", "Product or SKU code already exists")
        return self.get_product(session, product.id)

    def update_product(
        self,
        session: Session,
        product_id: int,
        payload: ProductUpdate,
    ) -> Product:
        self._lock_product(session, product_id)
        product = self.get_product(session, product_id)
        values = payload.model_dump(exclude_unset=True, exclude={"skus"})
        if "product_code" in values and values["product_code"] != product.product_code:
            raise ApiError(
                422,
                "immutable_product_code",
                "Product code is a stable identifier and cannot be changed",
            )
        if "category_id" in values:
            product.category = self.get_category(session, values["category_id"])
            values.pop("category_id")
        for field, value in values.items():
            setattr(product, field, value)
        if "skus" in payload.model_fields_set:
            self._sync_skus(session, product, payload.skus or [])
        self._validate_sku_specification_contract(product)
        self._validate_product(product)
        if product.status == "published":
            self._validate_publish(product)
        self._commit(session, "product_code_conflict", "Product or SKU code already exists")
        return self.get_product(session, product.id)

    def delete_product(self, session: Session, product_id: int) -> list[ObjectCleanupJob]:
        self._lock_product(session, product_id)
        product = self.get_product(session, product_id)
        if product.status == "published":
            raise ApiError(409, "published_product", "Unpublish the product before deleting it")
        object_keys = [image.object_key for image in product.images]
        cleanup_jobs = enqueue_object_cleanup(
            session,
            object_keys,
            reason="product_deleted",
        )
        session.delete(product)
        session.commit()
        return cleanup_jobs

    @staticmethod
    def serialize_product(product: Product, *, public_only: bool = False) -> ProductRead:
        skus = sorted(product.skus, key=lambda sku: (sku.sort_order, sku.id))
        if public_only:
            skus = [sku for sku in skus if sku.is_active]
        images = sorted(
            product.images,
            key=lambda item: (
                {"cover": 0, "gallery": 1, "detail": 2}[item.image_type],
                item.sort_order,
                item.id,
            ),
        )
        return ProductRead(
            id=product.id,
            product_code=product.product_code,
            name=product.name,
            subtitle=product.subtitle,
            category=CategoryRead.model_validate(product.category),
            status=product.status,
            base_price_cents=product.base_price_cents,
            market_price_cents=product.market_price_cents,
            currency=product.currency,
            unit=product.unit,
            description=product.description,
            featured=product.featured,
            stock_status=product.stock_status,
            inventory_count=product.inventory_count,
            tags=list(product.tags),
            selling_points=list(product.selling_points),
            specifications=list(product.specifications),
            ingredients=product.ingredients,
            allergen_info=product.allergen_info,
            sort_order=product.sort_order,
            skus=[ProductSkuRead.model_validate(sku) for sku in skus],
            images=[
                ProductImageRead(
                    id=image.id,
                    object_key=image.object_key,
                    image_type=image.image_type,
                    alt_text=image.alt_text,
                    sort_order=image.sort_order,
                    mime_type=image.mime_type,
                    size_bytes=image.size_bytes,
                    width=image.width,
                    height=image.height,
                    url=f"/api/v1/media/{quote(image.object_key, safe='/')}",
                    created_at=image.created_at,
                )
                for image in images
            ],
            created_at=product.created_at,
            updated_at=product.updated_at,
        )

    @staticmethod
    def validate_image_capacity(product: Product, image_type: str) -> None:
        limits = {"cover": 1, "gallery": 8, "detail": 20}
        current = sum(1 for image in product.images if image.image_type == image_type)
        if current >= limits[image_type]:
            raise ApiError(
                422,
                "image_limit_exceeded",
                f"Product already has the maximum number of {image_type} images",
            )

    @staticmethod
    def _lock_product(session: Session, product_id: int) -> None:
        found = session.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
        if found is None:
            raise ApiError(404, "product_not_found", "Product was not found")

    @staticmethod
    def _ensure_category_parent(session: Session, parent_id: int | None) -> None:
        if parent_id is not None and session.get(Category, parent_id) is None:
            raise ApiError(422, "category_parent_not_found", "Parent category was not found")

    @staticmethod
    def _ensure_no_category_cycle(
        session: Session,
        category: Category,
        new_parent_id: int | None,
    ) -> None:
        current_id = new_parent_id
        visited: set[int] = set()
        while current_id is not None:
            if current_id == category.id or current_id in visited:
                raise ApiError(422, "category_cycle", "Category hierarchy cannot contain a cycle")
            visited.add(current_id)
            parent = session.get(Category, current_id)
            current_id = parent.parent_id if parent is not None else None

    @staticmethod
    def _validate_sku_specification_contract(product: Product) -> None:
        try:
            specifications = [
                ProductSpecification.model_validate(item) for item in product.specifications
            ]
            skus = [
                ProductSkuInput(
                    sku_code=sku.sku_code,
                    name=sku.name,
                    price_cents=sku.price_cents,
                    market_price_cents=sku.market_price_cents,
                    stock_quantity=sku.stock_quantity,
                    attributes=dict(sku.attributes),
                    is_default=sku.is_default,
                    is_active=sku.is_active,
                    sort_order=sku.sort_order,
                )
                for sku in product.skus
            ]
            _validate_sku_attribute_references(specifications, skus)
        except (ValidationError, ValueError) as exc:
            raise ApiError(
                422,
                "invalid_sku_attributes",
                "SKU attributes must reference product specification options",
            ) from exc

    @staticmethod
    def _validate_product(product: Product) -> None:
        if (
            product.market_price_cents is not None
            and product.market_price_cents < product.base_price_cents
        ):
            raise ApiError(
                422,
                "invalid_market_price",
                "Market price must be greater than or equal to the current price",
            )
        active_defaults = [sku for sku in product.skus if sku.is_active and sku.is_default]
        if len(active_defaults) != 1:
            raise ApiError(
                422,
                "invalid_default_sku",
                "A product must have exactly one active default SKU",
            )

    @staticmethod
    def _validate_publish(product: Product) -> None:
        if not product.category.is_active:
            raise ApiError(422, "inactive_category", "Published product category must be active")
        covers = [image for image in product.images if image.image_type == "cover"]
        if len(covers) != 1:
            raise ApiError(
                422,
                "cover_required",
                "Published products must have exactly one cover image",
            )

    def _sync_skus(
        self,
        session: Session,
        product: Product,
        inputs: Sequence[ProductSkuInput],
    ) -> None:
        normalized_inputs = list(inputs)
        if not normalized_inputs:
            normalized_inputs = [
                ProductSkuInput(
                    sku_code=f"{product.product_code}-DEFAULT",
                    name="默认规格",
                    price_cents=product.base_price_cents,
                    market_price_cents=product.market_price_cents,
                    stock_quantity=product.inventory_count or 0,
                    is_default=True,
                )
            ]
        codes = [item.sku_code for item in normalized_inputs]
        if len(codes) != len(set(codes)):
            raise ApiError(422, "duplicate_sku_code", "SKU codes must be unique")
        if not any(item.is_active and item.is_default for item in normalized_inputs):
            first_active = next((item for item in normalized_inputs if item.is_active), None)
            if first_active is None:
                raise ApiError(422, "active_sku_required", "At least one SKU must be active")
            first_active.is_default = True
        if sum(item.is_active and item.is_default for item in normalized_inputs) != 1:
            raise ApiError(422, "invalid_default_sku", "Exactly one active SKU must be default")

        if product.id is not None:
            for sku in product.skus:
                if sku.is_default:
                    sku.is_default = False
            session.flush()
        existing = {sku.sku_code: sku for sku in product.skus}
        for item in normalized_inputs:
            sku = existing.pop(item.sku_code, None)
            if sku is None:
                sku = ProductSku(sku_code=item.sku_code)
                product.skus.append(sku)
            for field, value in item.model_dump().items():
                setattr(sku, field, value)
        for stale in existing.values():
            product.skus.remove(stale)
            session.delete(stale)

    @staticmethod
    def _commit(session: Session, code: str, message: str) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ApiError(409, code, message) from exc
