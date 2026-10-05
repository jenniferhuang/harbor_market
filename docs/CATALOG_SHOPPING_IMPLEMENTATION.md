# 商品选购实现

2026-10-05，根据主项目的 `docs/03商品选购.md` 和分类、详情、购物车原型实现。
代码位于 `wechat-miniprogram` 分支的 `miniprogram/`；界面继续使用港湾集市的绿色与中文文案。

## 界面与交互

- 左侧分类栏可收起和展开；点击分类跳到已加载分区，未加载分类从该分类开始浏览。
- 右侧按分类分区展示商品，每次请求最多20件。当前分类加载完成后，继续滑动或点击继续浏览加载下一分类；滚动位置同步左侧选中状态。
- 商品行展示真实封面、名称、副标题、价格、参考价、单位和供应状态。加号打开详情的规格选择弹层。
- 详情展示封面/轮播、商品亮点、介绍、配料、过敏原、单位、库存及启用的规格价格与库存。`detail` 图片独立按宽度展示，不混入轮播。
- 规格弹层选择单选/多选属性和数量，校验实际SKU与库存后加入购物车；SKU价格与附加选项价格按已有规则计算。
- 同一SKU的不同附加选项共享库存上限，购物车与规格弹层按合计已选数量计算还可添加的数量。
- 分类页和详情页底部显示购物车数量与合计。点击图标或合计展开已选商品，支持增减数量、减至0移除及确认清空。
- 购物车保存于本机；去结算当前显示“订单结算尚未开放”。尚未创建订单、预留库存或发起支付。

不生成销量、会员价、评价或运费文案；界面只使用后台实际已有字段。

## 共享后台

本次复用现有分类、商品、SKU、图片和导入功能，没有增加接口、表或迁移。

| 详情内容 | 后台字段 |
| --- | --- |
| 名称、介绍和标签 | name、subtitle、description、tags、selling_points |
| 类别和计价单位 | category、unit |
| 价格与参考价 | base_price_cents、market_price_cents；选中SKU的price_cents、market_price_cents |
| 供货和库存 | stock_status、inventory_count、SKU stock_quantity |
| 规格与附加选择 | specifications、SKU attributes |
| 配料和过敏原 | ingredients、allergen_info |
| 封面、轮播和图文详情 | images的cover、gallery、detail角色 |

小程序读取 `/api/v1/catalog/categories`、`/api/v1/catalog/products` 和按商品编码的详情接口。
后台维护入口是 `http://127.0.0.1:8080/admin/products`。只有已发布且类别启用的商品公开。

## 已准备的开发数据

持久开发数据库增加了 `SORGHUM_WINE / 高粱酒`、`PASTRY_PIE / 糕点馅饼`、
`DRIED_FISH_MEAT / 鱼干肉脯` 三个分类。原有果咖分类保留。

按原始照片能确认的内容创建了三款鱼类商品草稿：

| 编码 | 名称 | 图片 |
| --- | --- | --- |
| XM-CRISPY-HAIRTAIL | 香酥带鱼 | 封面1张、详情1张 |
| XM-FISH-BONE-BITES | 鱼骨粒 | 封面1张、详情1张 |
| XM-DRIED-FISH-UNSPECIFIED | 鱼干（品种未标注） | 封面1张 |

照片来自用户的“小妹店商品”目录，五张原图已通过图片校验并上传本机MinIO。
目录中没有可核实的高粱酒或糕点馅饼商品资料；没有用其他食品替代这两类。
现有资料没有售价、销售单位、规格或库存，因此这些商品保持草稿：
价格0、规格“待确认规格”和SKU数量0是技术占位，商品库存为null，实际信息仍未知。
**请在后台补齐真实价格、单位、规格和库存，再上架；占位0不是实际售价。**

按用户在后台会话的要求，苹果美式已归档，原记录与图片保留。
当前数据库为3款草稿、1款归档商品，公开商品数量为0，因此小程序显示暂无在售商品。

操作前已保存VM备份：
`/opt/harbor-market-development-db/backups/xiangyue_xiamen-before-catalog-shopping-20261005T133329Z.sql`
（67,543字节，权限600）。导入使用现有图片暂存及Excel校验/导入流程，重复执行复用已完成任务，未增加重复商品或图片。
五个暂存对象已清理，正式图片保留。数据库与图片可按现有导出/媒体迁移流程准备生产导入。

## 验证与本机预览

- 小程序自动化测试：215项通过；ESLint通过。
- 后台商品接口、属性与Excel导入回归：148项通过，使用隔离SQLite，不重置开发数据库。
- 微信开发者工具自带编译器：15个WXML、16个WXSS文件编译通过。
- 本机服务健康：数据库和对象存储均正常；公开接口隐藏草稿及归档商品。
- 本机接口联调：游客分类请求、顺序浏览四个实际分类、收起/展开及分类跳转通过。
- 模拟器窗口读取超时，视觉检查及真机检查尚未完成。

用户填入 `.env` 的AppID已同步到主项目和开发工作树各自被Git忽略的
`miniprogram/project.private.config.json`；跟踪配置仍保留touristappid，AppSecret不进入小程序。
本机开发者工具尝试打开时返回code10：`The appID is not Miniprogram/Minigame type, invalid app type`。
需要使用微信公众平台小程序账号的AppID；AppSecret仍为空，后端微信登录保持disabled。

开发者工具已打开独立的游客预览目录：
`/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market-wechat-miniprogram/.data/miniprogram-tourist-preview`。
此目录只包含本地源码副本和touristappid，不含实际AppID/AppSecret；它用于本机模拟器，不能证明真机预览或真实登录可用。
正常开发入口仍为工作树的 `miniprogram/`。

验证日志及编译输出位于本工作树忽略的 `output/catalog-shopping-verification/`。
后台导入工作簿、原图清单和操作报告位于后台工作树忽略的
`backend/.data/catalog-import-review/executed/`，最终结果是 `final-report.json`、`rerun-report.json` 和 `cleanup-report.json`。
