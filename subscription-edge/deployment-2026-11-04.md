# 2026-11-04 部署验收

已部署 `subscription-edge` Worker 和专用 KV，现有订阅域名、路径、token 保持不变。管理后台及节点生成继续位于 OCI；Google 不增加常驻服务。

## 入口与运行态

- `sub.lfdou.dpdns.org` 使用 Cloudflare 代理 CNAME，指向专用 `subscription-admin` Tunnel。
- `/share/*`、`/rules/*`、`/__publish*`、`/__health` 由 Worker 处理，其他路径经 Tunnel 到本机 Caddy，保留 Basic 登录。
- 临时 `workers.dev` 入口已关闭，Worker 不开启请求观测日志。
- OCI `subscription-edge-publish.timer` 已启用并开机自启，每 15 分钟刷新订阅与流量头；不持有 Cloudflare 平台 API 凭据。
- `cloudflared-subscription.service` 已启用并开机自启，四条 HTTP/2 连接健康；官方 ARM64 `cloudflared 2026.10.7` 二进制校验 SHA-256 后安装。

## 已执行验证

- Node 24 的 8 项 Worker 测试通过；本地发布器的 7 项测试通过，并在 OCI Python 3.14 重跑通过，OCI 额外将 ResourceWarning 视为错误。
- 本机绕过代理、绑定物理网卡通过 IPv4 下载 HTTP 200；正常本机下载路径也返回 HTTP 200。
- 同一局域网 `192.168.1.115` 的 IPv4、IPv6 访问，以及 Google 节点 IPv4 访问均成功。
- 21 条代理参数、分组、DNS 和分流规则的规范化摘要与源配置一致；仅改变规则发布地址，并删除旧规则域名的 IPv4 固定解析。
- 全部 31 份 MRS 规则共 704,718 字节，Cloudflare 下载后的 SHA-256 与已验证源文件一致。
- 未提供订阅 token 返回 401；未登录管理页面返回 401，原账号登录和后台 API 返回 200。
- 实际停止新建的管理 Tunnel 后，本机物理 IPv4 仍下载订阅 HTTP 200，全部 31 份规则下载和摘要检查通过；随后恢复 Tunnel。
- 第二次发布只更新订阅快照，没有重复上传未变化规则；日历定时触发已实际完成一次发布。
- 私有配置、发布凭据、Tunnel 凭据、部署元数据和源码已做本机 AES-GCM 加密备份，并验证解密一致。

## 边界

未更换 OCI 的公网 IPv4，也未修复其到当前网络的返回链路。代理流量继续使用原有 IPv4/IPv6 地址，配额保护保持原机制。Cloudflare 下载入口不依赖 OCI 在线，但管理和产生新版本仍需要 OCI；发布停止时返回最近有效版本，流量头可能落后。KV 跨地区传播需要时间，新增规则暂不可用时返回 503 供客户端重试。

源码和非敏感运维说明在本目录；真实 token、账号密码、平台授权与运行数据不放入 Git。
