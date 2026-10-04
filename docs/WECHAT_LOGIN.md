# 微信小程序登录

本功能对应主工作目录 `harbor_market/docs/01Login.md` 的登录需求和两张原型图。开发分支为 `wechat-miniprogram`；
小程序项目目录是该工作树的 `miniprogram/`，网页和小程序共用 FastAPI 后端。

## 使用流程

首次打开小程序，未登录用户会看到可关闭的登录弹窗。点击关闭或“暂不登录，继续逛逛”后，
仍可查看商品、选择规格和使用购物车。本次启动不会反复弹出；首页头像和底部“我的”可重新打开。

头像按钮使用微信原生 `chooseAvatar`，昵称使用 `type="nickname"` 输入框。两项都选填，昵称最多
64 个字符。点击“微信登录”才调用 `wx.login`；后端使用微信返回的临时 code 换取可信身份，并签发
本应用会话。头像和昵称保存失败时，已验证的登录仍有效，界面提示稍后到“我的”重试。

“我的”支持编辑资料和退出登录。启动时会向后端重新验证保存的会话；过期或被撤销时回到游客状态。
退出登录、登录过期和更改接口地址均保留设备购物车。更改接口地址会清除客户登录，避免把原服务器的
凭证发送到另一个服务器。网页和小程序的界面文字统一为中文。

## 真实微信登录配置

在后端使用的私有 `.env` 中填写：

```dotenv
WECHAT_MINIPROGRAM_AUTH_MODE=live
WECHAT_MINIPROGRAM_APP_ID=你的微信小程序AppID
WECHAT_MINIPROGRAM_APP_SECRET=你的微信小程序AppSecret
```

AppID 必须与微信开发者工具正在运行的项目一致。AppSecret 只放在后端环境文件中，不进入小程序源码、
Git、聊天或截图。配置后重新创建后端容器，让环境变量生效。`disabled` 模式或缺少有效凭证时，
后端返回“微信登录暂不可用”，不会创建模拟登录。`touristappid` 可浏览本地商品，但不能验证真实微信身份。

其余可选设置见根目录 `.env.example`：默认客户会话有效期七天，登录每个客户端每分钟十次，头像上限 2 MiB。
真实手机访问需要手机可达的接口域名。为正式版本配置微信的 request、uploadFile、downloadFile 合法域名。

私有头像通过带 Authorization 的 `wx.downloadFile` 下载，再用设备临时路径显示；头像不提供公开对象地址。
客户身份与网页后台管理员账户分开，客户凭证不能访问后台管理接口。

## 接口

统一前缀为 `/api/v1/mini/auth`。响应沿用 `{ "data": ... }`；除登录外均使用
`Authorization: Bearer <access_token>`。

| 方法与路径 | 请求 | 响应 data |
|---|---|---|
| POST `/login` | `{ "code": "微信临时code" }` | `access_token`, `token_type: "Bearer"`, `expires_at`, `customer` |
| GET `/me` | 无 | 当前 `customer` |
| PATCH `/profile` | `{ "nickname": "昵称" }` | 更新后的 `customer` |
| POST `/avatar` | multipart `file` | 更新后的 `customer` |
| GET `/avatar` | 无 | 当前客户的头像图片 |
| POST `/logout` | 无 | 退出结果；当前会话被撤销 |

`customer` 只包含 `id`、`nickname`、`avatar_url`。OpenID、微信 session_key、AppSecret 不返回给客户端。
数据库只保存本应用会话 token 的哈希；客户端会话绑定接口地址和到期时间。迟到的网络响应不会恢复已退出的账户。

## 数据与验证

Alembic `0005_add_mini_customer_sessions` 增加 `mini_customers` 与 `mini_sessions`，保留现有用户、商品、
图片和支付数据。头像使用现有私有 MinIO 存储，替换后的旧对象通过清理队列处理。
升级持久开发数据库前应创建受限备份；不能对该数据库运行会重置数据的迁移或并发测试。

本地代码检查：

```bash
cd miniprogram
npm test
npm run lint
cd ../frontend
npm test
npm run build
npm run lint
```

后端测试使用独立测试数据库和注入的微信服务替身。自动测试不代表真实微信登录已经验证。
在微信开发者工具中导入开发工作树的 `miniprogram/` 后编译，检查关闭弹窗、原生头像菜单、
昵称输入、微信登录、重启恢复、修改资料和退出。真实微信登录必须在 AppID/AppSecret 配置完成后验证。

## 当前 Mac 的开发预览

本次登录功能在 `harbor_market-wechat-miniprogram` 工作树上整合，主目录的 `main` 分支尚未合并该功能。
本地服务沿用主目录的私有 `.env`、持久开发数据库和原有 MinIO 数据。已构建本地镜像后，可从本开发
工作树启动或重新创建服务：

```bash
task_main_root=/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market
MINIO_DATA_DIR="$task_main_root/.data/local-preview/minio" \
  docker compose --env-file "$task_main_root/.env" \
  -f compose.yaml -f compose.development-db.yaml up -d --no-build
```

修改微信凭证后也从此工作树执行上述命令，使新增 Compose 配置传入后端。
为避免切换工作树时误用新的空目录，MinIO 路径明确指向原始绝对目录。
当前 `.env` 使用 `WECHAT_MINIPROGRAM_AUTH_MODE=disabled`，AppSecret 留空，待填写真实凭证后切换到 `live`。

2026-10-04 已部署本地预览并应用迁移 0005。健康检查、已发布商品及其 PNG 图片、未登录客户接口的
401 响应和关闭真实登录时的 503 响应均通过检查。85 项小程序测试、48 项网页测试和 420 项后端隔离测试通过。
微信开发者工具自动控制超时，尚未完成模拟器中的视觉检查；真实微信登录仍待有效凭证和设备验证。

本次 Docker 内的前端 npm 安装报告 `Exit handler never called`；本地 Node.js 构建和类型检查通过后，
使用生成的 `frontend/dist` 打包为相同 Nginx 运行时镜像。临时构建文件在主目录忽略的
`.data/login-preview/frontend/`，没有改变源码中的标准 Docker 构建流程。

本次只实现登录和客户资料。订单、收货地址、付款以及管理员草稿预览仍按各自需求继续开发。
