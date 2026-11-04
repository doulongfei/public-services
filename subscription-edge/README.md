# Cloudflare 订阅分发

Sub-Store 管理和生成继续在 OCI；Cloudflare Worker + 独立 KV 保存已验证的订阅和 MRS 规则。下载接口不回源，OCI 停机时仍返回最近有效版本。现有 `/share/file/main?token=...` 链接不变。未迁移代理流量。

## 实现与目录

- `worker.mjs`：订阅 token 校验、规则下载、独立发布密钥、上传大小和摘要检查。
- `publisher.py`：复用宿主机已有 PyYAML，读取本机 Sub-Store 和 `/var/lib/proxy-rules/current`；不需要 npm 或新的 Docker 容器。
- `/etc/subscription-edge/config.json`：root 0600，保存本机导出地址及发布密钥；不包含 Cloudflare 平台管理凭据。
- `/opt/subscription-edge/publisher.py`：受信任发布程序。
- `/var/lib/subscription-edge/state.json`：成功发布记录；规则仅在摘要变化时上传。
- `subscription-edge-publish.timer`：每 15 分钟刷新订阅和流量头。

Worker 仅路由 `sub.lfdou.dpdns.org/share/*`、`/rules/*`、`/__publish*`、`/__health`。其他路径经专用 `subscription-admin` Tunnel 到原 Caddy，保留管理页面的 Basic 登录。域名使用指向 Tunnel 的代理 CNAME；OCI 主动建立四条 HTTP/2 连接，不依赖公网入站 IPv4/IPv6 的回源路径。`oci.lfdou.dpdns.org` 和代理节点 IP 不开启 HTTP CDN 代理。

Tunnel 程序来自 Cloudflare 官方 ARM64 发布，并校验 GitHub 发布元数据中的 SHA-256。独立 `cloudflared-subscription.service` 使用动态用户、systemd 凭据、256 MiB 内存上限；不复用或修改已有 Tunnel。它只转发本机 `https://127.0.0.1:443`，设置正确的 Host/SNI，保留证书校验。下载订阅和规则不经过 Tunnel。

规则使用内容摘要地址，先上传全部规则，再提交唯一订阅快照。发布失败保留上一版本。KV 有跨地域传播延迟，新版本并非全球瞬时同步；新增规则未传播时返回 503 和 Retry-After，客户端重试，不会返回错误的规则内容。

订阅带 `Cache-Control: private, no-store`，未授权请求在读取订阅前拒绝。发布密钥与订阅 token 分离，Worker 只保存两者的 SHA-256 摘要。源码不输出请求 URL、密钥或节点参数。MRS 规则本身公开可下载，订阅和上传接口需要各自的授权。

## 数据有效期

最近有效快照不设置自动过期；超过一小时未刷新时响应头 `X-Subscription-Stale: true`，并保留最近更新时间。流量头是最近一次发布时的数据，实际停止代理仍由各节点的本地配额服务控制。Cloudflare 故障时，这个下载入口也可能不可达。

## 验证与运维

```sh
node --test test-worker.mjs
python3 -m unittest -v test_publisher.py
sudo systemctl start subscription-edge-publish.service
sudo systemctl status subscription-edge-publish.timer --no-pager
sudo systemctl status cloudflared-subscription.service --no-pager
sudo journalctl -u subscription-edge-publish.service --since today --no-pager
```

`GET /__health` 只返回是否存在快照及更新时间，不返回订阅内容。`HEAD /share/file/main?token=...` 使用相同 token 校验。禁用 Worker 路由并恢复迁移前 DNS 记录即可回退；旧 Sub-Store 数据与 token 不删除。

生产 Worker 关闭临时 `workers.dev` 入口。重新部署时复用专用 KV，并通过 Worker Secret 配置 `SUBSCRIPTION_TOKEN_SHA256`、`PUBLISH_TOKEN_SHA256`，不要将明文 token 或实际私有配置写入仓库。部署参数参考 `wrangler.example.jsonc`。
