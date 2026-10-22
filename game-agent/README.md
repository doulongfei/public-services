# 星港守卫：一周本地模型实验

- 试玩：https://game.doufei.eu.org/
- 进度、截图、反馈及暂停：https://lab.doufei.eu.org/
- 模型：OCI 上已部署的 Qwen3.6-35B-A3B UD-Q3_K_M，仅使用本地推理，不配置付费模型回退。

复用 Docker、Caddy 和 Pi 1.3.0。游戏用 Canvas 2D 和 ES Modules，不依赖外部素材或 CDN。Phaser 适合更复杂的场景管理；本实验的核心是让 16K 上下文的小模型分轮修改少量代码，先用浏览器已有的 Canvas API。

## 一轮的边界

1. 从当前已发布清单指定的 Git 提交导出候选。任务、候选路径、基线提交、错误、代码/基础设施失败次数和下次重试时间持久化到 `checkpoint.json`，进程重启继续原任务。
2. Pi 在内部 Docker 网络中调用 llama.cpp，最多 8 次模型回合、12 次文件工具调用、30 分钟。只开放 read/edit/write，没有 shell、SSH、GitHub 或 Docker 管理权限。模型和 Pi 使用真实 16K 上下文，摘要保留 6144 tokens、近期内容 2048 tokens，原始密钥只读挂载。
3. 必须收到 `agent_end`，最终助手消息 `stopReason=stop`，且至少一次 edit/write 工具成功。JSON 模式的进程退出码 0 不足以证明完成；报错、中止、输出截断、仅阅读均不接受。
4. 控制器检查路径、文件大小、私密值和离线约束。测试容器完全断网且没有模型密钥，执行固定规则测试、200 秒实际模拟、桌面键盘、手机触控、暂停/继续、渲染、布局和异常检查。
5. 每轮还需写 `game/acceptance.json`：1–4 个声明式 core/ui/visual 用例。候选必须通过全部既有用例，并至少有一个新用例在已发布基线上失败。仅注释、未加载文件或重复实现不能算有效改进。已接受用例复制到模型不可写的契约清单；后续轮次持续回归。最高战绩还固定检查已存数据、无效/不可用存储、真实得分保存、游戏结束/重开和刷新；敌机、补给、升级、波次、伤害、精英和护盾有独立功能契约。
6. 用私有 Git 索引和 `commit-tree` 准备提交，不移动已发布 HEAD。准备完整不可变目录、截图及发布清单，写入 `pending.json`，最后在控制 API 共享锁内复核截止/运行状态并切换指针。进程中断后，指针已切换则补记成功一次，未切换则继续保留旧版和待修候选。
7. HTML 由发布器注入固定版本的资源基址，模块、样式等都从 `/releases/<版本>/game/` 加载。周期内保留所有不可变版本，避免页面请求跨越发布时混用文件；截图也随同一发布指针切换。过期清理不参与发布成功判定。
8. 人工暂停/结束和进程中断不消耗代码失败次数，也不推进任务。模型故障/预算中止单独退避；代码验证连续失败三次则保留失败记录、从已发布源码重做同一任务。重试等待依次为 4/8/16/32/60 分钟，最多 1 小时，重启不会清零；正常轮次间隔 2 分钟。

验收格式示例：

```json
{"task":"weapon-upgrade","checks":[{"kind":"core","initial":{"score":100},"steps":1,"path":"level","op":"gt","value":1}]}
```

示例只说明字段格式，具体用例必须对应当前任务。core 从 playing 状态开始，合并 initial，以 1/60 秒执行 steps 次，最多 4000 次；input 支持移动和护盾。UI 使用单个元素 ID，phase 为 ready/playing/paused，property 为 text/visible。比较支持 eq/gt/gte/lt/lte/contains/exists。视觉用例为 `{"kind":"visual","selector":"#arena"}`，在相同随机种子和虚拟时钟下对比画布；后续允许继续改进画面，不固定整幅图的像素。

自动验收证明指定行为与回归条件，不代替对乐趣、平衡和视觉质量的试玩评价。既有版本升级运行器时先补跑固定功能验收，不伪造历史模型轮次为新流程成功。

## 时间、控制与资源

`/srv/game-agent/config.json` 保存一次性的绝对开始和截止时间。截止为开始后 168 小时，进程重启、暂停和恢复都不会延长；截止后保留游戏并停止迭代。除循环内检查外，`starharbor-deadline.timer` 按相同的绝对时间独立停机，Persistent=true 使错过的截止在服务器恢复后补执行。服务设置 Restart=on-failure，服务器重启后从已保存状态继续。用户点“结束任务”后不能从页面恢复。

模型沿用 2 核 / 18 GiB 配额；Pi 工作者为 0.5 核 / 768 MiB；测试为 0.75 核 / 1 GiB，开发和测试分阶段执行。发布控制器 256 MiB，控制 API 96 MiB。磁盘剩余小于 12 GiB 时暂停新轮次；候选及日志保留最近 30 轮，试玩发布目录保留整个运行周期的不可变版本，完整已接受源码历史保留在本地 Git。

反馈和暂停需要独立控制密钥 `/srv/game-agent/control/api-key`。浏览器只在当前页面内存中使用密钥，不写 localStorage。反馈内容由下一轮读取，不会修改绝对截止时间。控制 API 独立于游戏页面的 Origin，游戏设置 connect-src 'none'，不能调用任务控制接口。

```text
/opt/starharbor-agent/         # 受信任的运行器、固定测试、控制网页、镜像构建文件
/srv/game-agent/
├── config.json              # 固定截止时间和运行镜像 ID
├── control/                 # 独立密钥与运行/暂停/反馈指令
├── history/                 # 已发布源码的 Git 仓库，不挂载给模型
├── baselines/               # 按提交导出的只读基线
├── checkpoint.json          # 私有恢复检查点
├── contracts.json           # 私有已接受验收用例
├── pending.json             # 发布进行中时的事务记录
├── rounds/                  # 候选、私有 Pi 日志和测试输出
└── public/
    ├── current -> releases/...  # Caddy 使用的当前通过版本
    ├── state.json           # 无密钥的阶段和迭代记录
    └── releases/<版本>/     # game/、截图和不可变发布清单
```

## 运维与验证

运行镜像从固定摘要的 Node 24.22.1 ARM64 镜像构建，npm 依赖由 lock 文件固定。运行器配置保存实际构建 image ID，而不是跟随可变标签。`starharbor-model` 为 internal Docker 网络，只连接模型与临时 Pi 容器；测试使用 network=none。

```sh
python3 -m unittest discover -s game-agent/tests -p '*_test.py' -v
sudo systemctl status starharbor-agent starharbor-control --no-pager
sudo journalctl -u starharbor-agent -n 50 --no-pager
sudo systemctl stop starharbor-agent       # 紧急停机；不会删除已发布游戏
sudo systemctl start starharbor-agent      # 未到期且未在页面结束时继续原周期
```

不要在运行时执行 bootstrap.py 覆盖状态或重新设置截止时间。更改受信任的运行器前先暂停任务并做异机加密备份，保留历史、检查点、契约与控制文件。`control/decision.lock` 应由 game-control 用户拥有、权限 0600，供控制 API 与 root 发布器共同加锁。密钥、候选日志、运行时配置和数据不提交到公开仓库；本仓库保存可重建的基线与运行器。
