# 星港守卫：一周本地模型实验

- 试玩：https://game.doufei.eu.org/
- 进度、截图、反馈及暂停：https://lab.doufei.eu.org/
- 模型：OCI 上已部署的 Qwen3.6-35B-A3B UD-Q3_K_M，仅使用本地推理，不配置付费模型回退。

复用 Docker、Caddy 和 Pi 1.3.0。游戏用 Canvas 2D 和 ES Modules，不依赖外部素材或 CDN。Phaser 适合更复杂的场景管理；本实验的核心是让 16K 上下文的小模型分轮修改少量代码，先用浏览器已有的 Canvas API。

## 一轮的边界

1. 从最后通过测试的源码复制候选目录；每轮选择一个小任务，优先读取所有者最近提交的反馈。模型与 Pi 都声明真实的 16K 上下文；Pi 会额外预留 4096 tokens，不能按 8K 配置后仍假定可读取接近 8K 的源码。
2. Pi 在内部 Docker 网络中调用 llama.cpp，最多 8 次模型回合、12 次文件工具调用、30 分钟。只开放 read/edit/write，限制为游戏文件和 NOTES.md；没有 shell、SSH、GitHub 或 Docker 管理权限。为 16K 模型显式设置摘要保留 6144 tokens、近期内容 2048 tokens；临时认证缓存写入容器内存盘，原始密钥保持只读。
3. 受信任的控制器检查路径、文件大小、私密值和离线资源约束，再启动完全断网、没有模型密钥的测试容器。
4. 固定测试检查规则、碰撞、状态、实体数量，以及真实 Chromium 的桌面键盘、手机触控、暂停/继续、Canvas 渲染、布局和页面异常。
5. 只有源码实际变更且所有检查通过，才提交服务器本地 Git 历史，并原子切换试玩版本。模型不能修改固定测试、发布器、服务配置或截止时间。
6. 未通过的候选保留以便下轮修复；每连续 3 次失败退回最后良好版本并换一个任务。失败退避最多 1 小时，正常轮次之间间隔 2 分钟。

首轮任务是可观察的暂停提示层，额外检查 `#pause-message` 在暂停时显示、继续后隐藏。后续改动必须继续保留这项契约。第一轮方向清单包括最高战绩、敌机种类、升级、补给、触控、难度和自主审查；走完清单后，由模型根据当前源码、NOTES.md 和用户反馈自行选择下一项改进，原清单只作为可选审查角度。通过回归测试不等于每个新玩法都经过人工验收；试玩与反馈仍是质量判断的一部分。

## 时间、控制与资源

`/srv/game-agent/config.json` 保存一次性的绝对开始和截止时间。截止为开始后 168 小时，进程重启、暂停和恢复都不会延长；截止后保留游戏并停止迭代。除循环内检查外，`starharbor-deadline.timer` 按相同的绝对时间独立停机，Persistent=true 使错过的截止在服务器恢复后补执行。服务设置 Restart=on-failure，服务器重启后从已保存状态继续。用户点“结束任务”后不能从页面恢复。

模型沿用 2 核 / 18 GiB 配额；Pi 工作者为 0.5 核 / 768 MiB；测试为 0.75 核 / 1 GiB，开发和测试分阶段执行。发布控制器 256 MiB，控制 API 96 MiB。磁盘剩余小于 12 GiB 时暂停新轮次；候选及日志保留最近 30 轮，试玩发布目录保留最近 25 版，完整已接受源码历史保留在本地 Git。

反馈和暂停需要独立控制密钥 `/srv/game-agent/control/api-key`。浏览器只在当前页面内存中使用密钥，不写 localStorage。反馈内容由下一轮读取，不会修改绝对截止时间。控制 API 独立于游戏页面的 Origin，游戏设置 connect-src 'none'，不能调用任务控制接口。

```text
/opt/starharbor-agent/         # 受信任的运行器、固定测试、控制网页、镜像构建文件
/srv/game-agent/
├── config.json              # 固定截止时间和运行镜像 ID
├── control/                 # 独立密钥与运行/暂停/反馈指令
├── history/                 # 已接受源码的 Git 仓库，不挂载给模型
├── rounds/                  # 候选、私有 Pi 日志和测试输出
└── public/
    ├── current -> releases/...  # Caddy 使用的当前通过版本
    ├── releases/            # 有限的可发布历史
    ├── state.json           # 无密钥的阶段和迭代记录
    ├── desktop.png
    └── mobile.png
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

不要在运行时执行 bootstrap.py 覆盖状态或重新设置截止时间。更改受信任的运行器前先暂停任务，保留历史与控制文件。密钥、候选日志、运行时配置和数据不提交到公开仓库；本仓库保存可重建的基线与运行器。
