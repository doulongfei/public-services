# LF 公开工具服务

在 OCI Ampere A1 上部署的免费公开工具。使用各项目的开源版本，保留作者署名、许可证和对应源码。

| 服务 | 实际访问地址 | 源码 |
| --- | --- | --- |
| IT-Tools | https://tools.lfdou.ccwu.cc/ | https://github.com/doulongfei/it-tools |
| BentoPDF | https://pdf.lfdou.ccwu.cc/ | https://github.com/doulongfei/bentopdf |
| LibreTranslate | https://tr.lfdou.ccwu.cc/ | https://github.com/doulongfei/LibreTranslate |

IT-Tools 大多在浏览器计算；BentoPDF 的文件处理在浏览器本地完成；LibreTranslate 在服务器完成中英翻译。公开部署需要真实访问才能产生业务负载，不能保证避免免费实例闲置回收。

## Docker 选型

服务器已运行 Docker 和 Sub-Store，因此复用现有 Docker Engine 与 Compose。每个应用保留独立容器、资源上限和源码版本，宿主机 Caddy 负责 HTTPS。直接安装 Node、Python、翻译模型及不同项目依赖，会增加宿主机的依赖耦合；本方案将这些依赖放在各自镜像中。

应用从本账号的源码 fork 构建。镜像标签使用 Git 提交号，不以可变的 latest 作为生产发布记录。`release.json` 记录已验证的源码版本和实际镜像 ID。构建与升级逐项执行，运行时资源限制不代表构建过程的峰值。

## 目录

```text
/srv/public-services/
├── source/
│   ├── it-tools/           # doulongfei/it-tools 的源码
│   ├── bentopdf/           # doulongfei/bentopdf 的源码
│   └── libretranslate/     # doulongfei/LibreTranslate 的源码
├── stack/                  # 本部署仓库
│   ├── compose.yaml
│   ├── Caddyfile           # 仅包含三个公开站点，不含其他服务的凭据
│   ├── .env               # 当前源码提交号，Git 忽略
│   └── release.json        # 已发布版本清单
└── data/
    └── libretranslate/
        ├── models/         # 翻译模型，持久化，按需重新下载
        ├── cache/          # 模型和分词运行缓存
        └── db/             # 运行状态和数据库
```

宿主机入口配置在 `/etc/caddy/Caddyfile`，通过 import 引入 stack/Caddyfile。证书继续由宿主机 Caddy 管理。运行数据、数据库、缓存和凭据不提交到 GitHub。现有代理、订阅、统计与配额服务继续使用各自目录。

## 部署与升级

将本仓库克隆到 `/srv/public-services/stack`，将三个应用仓库分别克隆到 source 对应目录。先检查所选提交的改动，在 `.env` 记录三个完整提交号，再执行：

```sh
cd /srv/public-services/stack
docker compose config --quiet
docker compose build it-tools
docker compose build bentopdf
docker compose build libretranslate
docker compose up -d
docker compose ps
```

数据目录由 root 创建，LibreTranslate 的 models/cache/db 目录交给 UID/GID 1032，权限 0700。Caddy 配置验证通过后再 reload。应用端口 18101、18102、18103 只绑定 127.0.0.1，外部统一使用现有 80/443。

每次升级一个应用：保留旧镜像，记录旧提交号，检出新提交，修改 `.env` 对应值，执行 `docker compose build <服务>` 和 `docker compose up -d --no-deps <服务>`，完成实际功能验证后更新 release.json。失败时将对应源码与 `.env` 恢复为 release.json 记录的旧版本，使用保留镜像重新启动该服务。不要使用 `down -v` 删除数据。

## 资源与公开访问

IT-Tools 与 BentoPDF 各限制 256 MiB 内存；翻译服务限制 4 GiB、1.25 核，单进程执行翻译，先只加载中英模型。上述是本部署的初始资源预算，后续根据实际峰值调整。

翻译每次最多 2000 字符、单批一段，每个来源地址每分钟 6 次、每小时 60 次、每天 200 次；文件翻译关闭。Caddy 覆盖外部传入的 X-Forwarded-For，再由应用按真实来源地址计数。DNS 当前直连 OCI；将来开启 CDN 代理前，必须先配置可信代理和真实 IP 解析。

公开服务和现有服务共享服务器出站流量保护。静态站点保留上游浏览器安全头；BentoPDF 的 COOP/COEP 头通过反向代理保留，用于浏览器内的 Office/WASM 处理。应用内部管理接口和指标不对公众开放。

## 备份

源码和非敏感配置通过 GitHub 保存；模型和缓存可重新下载。数据库应使用 SQLite 在线备份方式导出，再与私有配置一起加入现有的异机加密备份。密钥只留在本机和 Mac，服务器不保存解密密钥。升级前先备份，不能把容器本身当作数据备份。

## 许可与来源

- IT-Tools：上游 https://github.com/sharevb/it-tools ，GPL-3.0。
- BentoPDF：上游 https://github.com/alam00000/bentopdf ，使用 AGPL-3.0 开源版本。
- LibreTranslate：上游 https://github.com/LibreTranslate/LibreTranslate ，AGPL-3.0。

各项目的 LICENSE 保留在对应源码仓库，定制源码可以从上表仓库获取。本部署仓库自己的配置和文档使用 MIT License。
