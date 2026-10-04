# 首页接口约定

接口均以 `/api/v1` 为前缀，成功返回 `{ "data": ... }`，错误返回
`{ "error": { "code": "...", "message": "中文提示" } }`。金额使用整数分，时间使用带时区的
ISO8601。分页参数 `page=1`、`page_size=20`，每页最多100条，列表响应仍为数组。

## 公开页面

- `GET /shop/home` 返回
  `{store,carousel,coupons,categories,hot_products,hot_products_source}`。
  `store` 为 `{name,phone,address,latitude,longitude,announcement_image_url}`；
  `carousel` 为 `[{id,title,media_type:"image"|"video",url}]`；
  `coupons` 为 `[{id,title,min_spend_cents,discount_cents,starts_at,expires_at}]`；
  `categories` 为 `[{id,code,name,image_url}]`；
  `hot_products` 为 `[{product:ProductRead,sold_quantity,order_count,repeat_purchase_count}]`。
  `hot_products_source` 为 `sales`、`featured` 或 `newest`，推荐来源如实返回。
- `GET /shop/hot-searches?limit=10` 返回 `[{product:ProductRead,search_hit_count}]`。
  只返回命中数大于0、分类启用且已发布的商品。
- `POST /shop/search`，JSON `{q,page:1,page_size:10}`，返回现有
  `ProductListData={items,total,page,page_size}`。每次显式提交为所有匹配的公开商品各加一次命中；
  继续翻页使用现有 `GET /catalog/products?q=...`，不会再累计。空搜索返回422。
  每个客户端IP默认每60秒最多30次提交（包括无效请求），超过返回429和 `Retry-After`。
  `SHOP_SEARCH_RATE_LIMIT` / `SHOP_SEARCH_RATE_WINDOW_SECONDS` 可调整；限流按进程执行。
- `GET /shop/media/{id}` 提供启用的图片或MP4；视频支持单段字节 `Range`。

`ProductRead` 与现有公开商品接口相同。销量直接汇总已完成的历史订单明细；作废订单不参与。
同一小程序客户购买同一商品的已完成订单数减去首次购买，就是该客户贡献的复购次数；匿名历史
订单计入销量和订单数，不计入复购。没有可展示销量时，返回真实的精选或新发布商品，计数为0。

## 小程序用户（Authorization: Bearer）

- `GET /mini/shop/me` → `{can_manage_store:boolean}`。
- `GET /mini/shop/favorites` → `ProductRead[]`。
- `GET|PUT|DELETE /mini/shop/favorites/{product_code}` → `{is_favorite:boolean}`。
- `GET /mini/shop/coupons` →
  `[{coupon_id,title,min_spend_cents,discount_cents,starts_at,expires_at,is_active,claimed_at}]`。
- `POST /mini/shop/coupons/{id}/claim` → 上述单条已领取记录；重复领取返回同一记录。
  列表显示当前有效活动并标记已领取状态；活动过期或停用后，已有领取记录的重试仍返回原领取时间，
  新领取则返回404。
- `GET /mini/shop/orders` → 当前客户的历史订单数组。
- 商家专用 `GET|PATCH /mini/shop/store` → 公开 `store` 字段；PATCH只接受
  `name,phone,address,latitude,longitude`。
- 商家专用 `POST /mini/shop/store/announcement`，multipart `file` → 更新后的 `store`。

商家身份由后台把真实的小程序客户ID绑定到唯一店铺；小程序不能自行指定店主或修改所有者。
优惠券只管理展示和领取，本阶段不执行核销或结算。

## 浏览器后台（管理员Cookie）

- `GET|PATCH /admin/shop/store` → 公开店铺字段加 `owner_customer_id`；PATCH可绑定或解除店主。
- `GET /admin/shop/customers` → `[{id,nickname}]`，支持有界分页。
- `GET /admin/shop/media` → `[{id,kind,category_id,title,sort_order,is_active,url,media_type}]`。
  `page=1&page_size=20`；后台需要更多资源时可指定 `page_size=100` 或继续翻页。
- `POST /admin/shop/media`，multipart
  `file,kind:carousel|category|announcement,title?,category_id?,sort_order?,is_active?` → 单条资源。
- `PATCH /admin/shop/media/{id}`，JSON `title?,sort_order?,is_active?` → 单条资源。
- `DELETE /admin/shop/media/{id}` → `{deleted:true}`。
- `GET|POST /admin/shop/coupons`；`PATCH /admin/shop/coupons/{id}`。
  创建字段：`title,min_spend_cents,discount_cents,starts_at,expires_at,is_active?`。
- `GET|POST /admin/shop/orders`；`POST /admin/shop/orders/{id}/void` → 历史订单。
  创建JSON：`{external_reference,customer_id?:int|null,completed_at?:ISO8601,
  items:[{product_code,quantity,unit_price_cents}]}`。

订单返回字段：`{id,external_reference,customer_id,completed_at,status:"completed"|"voided",
total_cents,items:[{product_code,product_name,quantity,unit_price_cents,line_total_cents}]}`。
名称和商品代码由服务器读取并保存快照；管理员提供历史成交单价和数量，服务器计算各行和总额。
唯一外部单号保证同一历史记录不会重复计入；重复且一致的提交返回已有订单，冲突返回409。
这些是管理员记录的已完成历史订单，不代表微信付款，不操作库存。

公告和分类代表图只接收静态图片。轮播接收静态图片或不超过60秒的MP4；具体大小限制由
`SHOP_IMAGE_UPLOAD_MAX_BYTES`（默认5MiB）和 `SHOP_VIDEO_UPLOAD_MAX_BYTES`（默认10MiB）指定。
所有资源复用已有MinIO和删除重试机制。本功能新增迁移0006，开发期间只运行隔离测试。
店铺首次未配置时显示默认名字“港湾集市”，其余联系方式和地址为空，不伪造导航位置。
