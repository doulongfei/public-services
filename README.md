# LF 公开工具服务

在 OCI Ampere A1 上部署的免费公开工具。使用各项目的开源版本，保留作者署名、许可证和对应源码。

| 服务 | 实际访问地址 | doufei.eu.org 入口 | 源码 |
| --- | --- | --- | --- |
| IT-Tools | https://tools.lfdou.ccwu.cc/ | https://tools.doufei.eu.org/ | https://github.com/doulongfei/it-tools |
| BentoPDF | https://pdf.lfdou.ccwu.cc/ | https://pdf.doufei.eu.org/ | https://github.com/doulongfei/bentopdf |
| LibreTranslate | https://tr.lfdou.ccwu.cc/ | https://tr.doufei.eu.org/ | https://github.com/doulongfei/LibreTranslate |

两组域名直接提供相同服务，复用同一组容器、数据与限流计数。应用的 canonical 继续使用 lfdou.ccwu.cc 地址；博客的“工具”菜单使用便于记忆的 doufei.eu.org 入口。两组域名均配置 IPv4、IPv6 和 HTTPS。

IT-Tools 大多在浏览器计算；BentoPDF 的文件处理在浏览器本地完成；LibreTranslate 在服务器完成中英翻译。公开部署需要真实访问才能产生业务负载，不能保证避免免费实例闲置回收。

小游戏“星港守卫”已加入同一台 OCI：试玩 https://game.doufei.eu.org/ ，进度与所有者控制 https://lab.doufei.eu.org/ 。一周内由本地模型分轮改进，通过固定测试才自动发布；运行时间、目录、限制和运维见 [game-agent/README.md](game-agent/README.md)。

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
│   ├── Caddyfile           # 公开站点和模型入口，不含其他服务的凭据
│   ├── .env               # 当前源码提交号，Git 忽略
│   └── release.json        # 已发布版本清单
└── data/
    ├── llama/
    │   ├── models/         # 固定版本的 GGUF 模型
    │   ├── secrets/        # API Key，root 管理，Git 忽略
    │   └── logs/           # 下载和诊断记录
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

翻译页面默认“自动检测 → 英语”，直接输入中文即可翻译。英文译中文时选择“简体中文”；语言列表使用 `zh-Hans`，不要将旧的模型代码 `zh` 写入前端默认目标，否则下拉框无法选中有效语言。明确选择与原文相同的目标语言时，API 按上游行为返回原文。

公开服务和现有服务共享服务器出站流量保护。静态站点保留上游浏览器安全头；BentoPDF 的 COOP/COEP 头通过反向代理保留，用于浏览器内的 Office/WASM 处理。应用内部管理接口和指标不对公众开放。

## 备份

源码和非敏感配置通过 GitHub 保存；模型和缓存可重新下载。Compose、Caddy 配置、私有环境文件和 release.json 已加入现有的异机加密备份，翻译目录中的 SQLite 数据库使用在线备份接口逐个导出。备份解密密钥只留在本机和 Mac，服务器不保存解密密钥。升级前先备份，不能把容器本身当作数据备份。

## 本地模型服务

聊天网页：https://ai.doufei.eu.org/ ；OpenAI 兼容 API：`https://ai.doufei.eu.org/v1`。
网页需要在设置中填写 API Key。聊天记录由上游网页保存在浏览器；不要在共享浏览器保存密钥。

复用宿主机 Caddy 和官方 llama.cpp ARM64 CPU 镜像，镜像使用 SHA-256 固定版本。模型为 Qwen3.6-35B-A3B 的 Unsloth UD-Q3_K_M 量化，权重约 15.46 GiB；模型仓库、提交、大小和 SHA-256 在 `llama-model.json` 中记录。本次只部署文本能力，不加载视觉投影文件。

模型启用独立的 `llm` Compose profile。初始化时创建数据目录，运行固定版本下载和校验脚本：

```sh
sudo install -d -m 0755 /srv/public-services/data/llama/models
sudo install -d -m 0700 /srv/public-services/data/llama/secrets
sudo python3 scripts/fetch-llama-model.py \
  --manifest llama-model.json --directory /srv/public-services/data/llama/models
sudo python3 - <<'PY'
import os, secrets
from pathlib import Path
p = Path('/srv/public-services/data/llama/secrets/api-key')
if not p.exists():
    p.write_text(secrets.token_urlsafe(32) + '\n')
p.chmod(0o600)
os.chown(p, 1000, 1000)
PY
docker compose --profile llm config --quiet
docker network inspect starharbor-model >/dev/null 2>&1 || docker network create --internal starharbor-model
docker compose --profile llm up -d --no-deps llama
docker compose --profile llm ps llama
```

服务以 UID/GID 1000 运行，只读挂载模型和密钥；端口 18104 只绑定回环地址。使用 2 核 CPU、18 GiB 内存、16384 token 上下文、单并发，默认关闭思考模式，默认最多输出 1024 token。显式请求较大输出仍受到上下文窗口约束。禁用权重重排，控制 ARM CPU 加载时的内存峰值；内存达到容器上限时可能终止模型进程，Docker 会按重启策略恢复。对外入口隐藏指标和管理路由；模型没有宿主机命令执行能力，工具由调用方客户端执行。

API Key 在服务器 `data/llama/secrets/api-key` 中，不能提交到仓库；已加入现有异机加密备份的输入清单，并在本机保留权限为 0600 的副本。API 请求使用 `Authorization: Bearer <API_KEY>`。本机 Pi 可以添加以下 provider（密钥通过环境变量传入）：

```json
{
  "providers": {
    "oci": {
      "baseUrl": "https://ai.doufei.eu.org/v1",
      "api": "openai-completions",
      "apiKey": "${OCI_LLAMA_API_KEY}",
      "models": [{
        "id": "qwen3.6-35b-a3b-q3_k_m",
        "reasoning": false,
        "input": ["text"],
        "contextWindow": 16384,
        "maxTokens": 2048,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
      }]
    }
  }
}
```

配置后运行 `pi --provider oci --model qwen3.6-35b-a3b-q3_k_m`。首次处理长提示词比连续输出慢，适合单用户基础测试；短请求速度不代表大型代码库或一周连续 Agent 任务的性能。使用 `docker compose --profile llm stop llama` 可以释放模型内存，不影响三个工具服务。

2026-10-22 基础验证：中文问答、SSE 流式输出、缺失和错误密钥返回 401、OpenAI 函数调用及结果续答、浏览器实际聊天均通过。Pi 1.3.0 实际读取文件并生成 `sum.js` 和计算结果，生成代码通过 5 项断言。2 核配额下短请求生成约 4 tokens/秒；Pi 的首次 614 token 提示词及工具请求约 86 秒，完整的三次模型请求约 124 秒。这是关闭扩展、MCP 和项目上下文后的最小读写任务，不能等同于完整项目 Agent 的性能。

## 已完成验证

2026-10-22 首次部署已验证三个 ARM64 容器的健康状态、资源限制、只读根文件系统和回环端口绑定；HTTPS、HTTP 跳转及从 Mac 发起的 IPv6 HTTPS 访问均通过。

- IT-Tools：中文 Base64 编解码、390 px 手机宽度显示。
- BentoPDF：两个 PDF 合并后实际解析为两页，文本内容与顺序正确；浏览器隔离状态和本站源码链接正常。
- LibreTranslate：页面自动翻译、中英双向 API、字符与批量限制、64 KB 请求体限制、请求频率限制，以及伪造转发头无法绕过限流。
- 现有 Caddy、代理、订阅、统计和流量保护服务状态正常。

这些是代表性功能验证，不代表逐项测试了上游的所有工具；本次没有重启服务器或修改 OCI 实例规格。

## 许可与来源

- IT-Tools：上游 https://github.com/sharevb/it-tools ，GPL-3.0。
- BentoPDF：上游 https://github.com/alam00000/bentopdf ，使用 AGPL-3.0 开源版本。
- LibreTranslate：上游 https://github.com/LibreTranslate/LibreTranslate ，AGPL-3.0。

各项目的 LICENSE 保留在对应源码仓库，定制源码可以从上表仓库获取。本部署仓库自己的配置和文档使用 MIT License。
