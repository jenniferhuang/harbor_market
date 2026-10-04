const errorMessages: Record<string, string> = {
  authentication_required: '请先登录后再继续。',
  invalid_credentials: '用户名或密码不正确。',
  username_unavailable: '该用户名已被注册，请更换用户名。',
  username_taken: '该用户名已被注册，请更换用户名。',
  admin_required: '需要管理员权限才能执行此操作。',
  csrf_origin_mismatch: '当前访问地址未获授权，请从正确的地址打开页面。',
  rate_limit_exceeded: '操作过于频繁，请稍后再试。',
  validation_error: '请检查并修改标出的字段。',
  immutable_category_code: '类目编码创建后不能修改。',
  invalid_category_parent: '类目不能将自身设为父级。',
  category_in_use: '该类目仍有关联商品或子类目，无法删除。',
  category_not_found: '未找到该类目，请刷新后重试。',
  category_parent_not_found: '未找到父级类目，请重新选择。',
  category_cycle: '类目层级不能形成循环。',
  category_code_conflict: '该类目编码已存在，请使用其他编码。',
  product_not_found: '未找到该商品，请刷新后重试。',
  immutable_product_code: '商品编码创建后不能修改。',
  product_code_conflict: '商品或销售规格编码已存在，请使用其他编码。',
  published_product: '请先下架商品，再执行删除操作。',
  image_limit_exceeded: '该类型的商品图片已达到数量上限。',
  invalid_market_price: '划线价不能低于当前售价。',
  invalid_default_sku: '商品必须且只能设置一个启用的默认销售规格。',
  inactive_category: '请先启用商品所属类目，再上架商品。',
  cover_required: '上架商品必须且只能设置一张封面图。',
  duplicate_sku_code: '销售规格编码不能重复。',
  invalid_sku_attributes: '销售规格属性必须对应商品的规格选项。',
  active_sku_required: '请至少启用一个销售规格。',
  invalid_product_code: '商品编码格式不正确，请检查后重试。',
  image_too_large: '图片超过允许的大小，请压缩后重新上传。',
  invalid_image: '图片为空或无法识别，请选择有效的图片。',
  invalid_image_dimensions: '图片尺寸超过允许范围，请调整后重新上传。',
  unsupported_image: '仅支持 JPEG、PNG 和 WebP 图片。',
  invalid_alt_text: '图片说明格式不正确，请检查后重试。',
  staging_quota_exceeded: '暂存图片已达到上限，请清理后重试。',
  invalid_staging_key: '暂存图片路径无效，请重新上传图片。',
  staging_key_in_use: '该图片已关联商品，请在商品中管理。',
  invalid_excel: '请选择有效的 .xlsx 工作簿。',
  excel_too_large: '工作簿超过 10 MiB，请减少数据后重试。',
  import_job_not_found: '未找到该导入任务，请刷新后重试。',
  idempotency_key_conflict: '该导入请求已用于其他工作簿，请重新选择文件。',
  import_in_progress: '原导入任务仍在处理中，请稍后重试。',
  image_not_found: '未找到该商品图片，请刷新后重试。',
  image_conflict: '图片关联失败，请刷新后重试。',
  published_cover: '请先下架商品，再删除封面图。',
  invalid_cleanup_status: '清理任务状态无效，请重新选择。',
  cleanup_job_not_found: '未找到该清理任务，请刷新后重试。',
  cleanup_pending: '数据已保存，图片清理仍待重试，请查看下方清理任务。',
  storage_unavailable: '图片存储服务暂不可用，请稍后重试。',
  database_unavailable: '数据服务暂不可用，请稍后重试。',
  media_not_found: '未找到该图片，请刷新后重试。',
  media_integrity_error: '图片数据异常，请重新上传或联系管理员。',
}

const fieldLabels: Record<string, string> = {
  username: '用户名', password: '密码', confirmPassword: '确认密码',
  product_code: '商品编码', category_code: '类目编码', sku_code: '销售规格编码',
  name: '名称', subtitle: '副标题', category_id: '类目', code: '编码',
  description: '说明', parent_id: '父级类目', sort_order: '排序值',
  is_active: '启用状态', status: '状态', base_price_cents: '基础价',
  market_price_cents: '划线价', price_cents: '售价', currency: '币种', unit: '销售单位',
  stock_status: '库存状态', stock_quantity: '库存数量', inventory_count: '库存数量',
  featured: '推荐状态', tags: '标签', selling_points: '卖点', ingredients: '配料',
  allergen_info: '过敏原信息', specifications: '规格', specifications_json: '规格数据',
  skus: '销售规格', attributes: '规格属性', attributes_json: '规格属性数据',
  selection_mode: '选择方式', required: '必选状态', min_select: '最少选择数',
  max_select: '最多选择数', options: '规格选项', price_delta_cents: '附加价',
  sort: '排序值', is_default: '默认状态', image_type: '图片类型', alt_text: '图片说明',
  images: '图片', object_key: '图片路径', file: '文件', sheet: '工作表', headers: '表头',
  rows: '行数', request: '请求', transaction: '导入事务', staging: '暂存图片', cleanup: '清理任务',
}

function ownLabel(labels: Record<string, string>, key: string): string | undefined {
  return Object.hasOwn(labels, key) ? labels[key] : undefined
}

export function fieldLabel(field: string | undefined): string {
  if (!field) return '—'
  return field.split('/').map((part) => {
    const segments = part.split('.').filter((segment) => !/^\d+$/.test(segment))
    return segments.map((segment) => ownLabel(fieldLabels, segment) ?? '字段').join(' · ')
  }).join(' / ')
}

export function chineseMessage(message: string | undefined, fallback: string): string {
  if (!message) return fallback
  const normalized = message.replaceAll(/\s+/g, ' ').trim()
  return /[\u3400-\u9fff]/.test(normalized) && normalized.length <= 240 ? normalized : fallback
}

export function apiErrorMessage(status: number, message: string, code?: string): string {
  const translated = code ? ownLabel(errorMessages, code.toLowerCase()) : undefined
  if (translated) return translated
  if (status === 0) return '无法连接服务器，请检查网络连接后重试。'
  if (status === 401) return '登录状态已失效，请重新登录。'
  if (status === 403) return '当前账号无权执行此操作。'
  if (status === 404) return '未找到所需内容，请刷新后重试。'
  if (status === 409) return chineseMessage(message, '数据已存在或发生冲突，请检查后重试。')
  if (status === 413) return '上传文件过大，请减小文件后重试。'
  if (status === 422) return chineseMessage(message, '请检查并修改标出的字段。')
  if (status === 429) return '操作过于频繁，请稍后再试。'
  if (status >= 500) return chineseMessage(message, '服务暂时不可用，请稍后重试。')
  return chineseMessage(message, '操作未能完成，请稍后重试。')
}

export function fieldErrorMessage(field: string, message: string): string {
  const label = fieldLabel(field)
  if (/[\u3400-\u9fff]/.test(message)) return chineseMessage(message, `${label}格式不正确。`)
  if (/passwords? do not match/i.test(message)) return '两次输入的密码不一致。'
  if (/already (registered|taken)/i.test(message)) return '该用户名已被注册。'
  if (/field required|is required|missing/i.test(message)) return `请填写${label}。`
  const minLength = /at least (\d+) characters/i.exec(message)
  if (minLength) return `${label}至少需要 ${minLength[1]} 个字符。`
  const maxLength = /at most (\d+) characters/i.exec(message)
  if (maxLength) return `${label}最多允许 ${maxLength[1]} 个字符。`
  if (field === 'username' && /pattern/i.test(message)) {
    return '用户名须以小写字母或数字开头，仅可包含小写字母、数字、点、下划线和连字符。'
  }
  const minimum = /greater than or equal to (-?\d+(?:\.\d+)?)/i.exec(message)
  if (minimum) return `${label}不能小于 ${minimum[1]}。`
  const maximum = /less than or equal to (-?\d+(?:\.\d+)?)/i.exec(message)
  if (maximum) return `${label}不能大于 ${maximum[1]}。`
  if (/valid integer/i.test(message)) return `${label}必须是整数。`
  if (/valid (number|decimal)/i.test(message)) return `${label}必须是数字。`
  return `${label}格式不正确，请检查后重试。`
}
