# 用安卓手机独立运行墨境

世界资料、角色和冒险存档保存在手机。真实模型旅程联网访问你填写的兼容 Chat Completions API。这套试玩入口需要在 Termux 启动本机引擎，再用同一部手机的浏览器游玩。

## 第一次安装

在安卓手机上安装 [F-Droid 的 Termux](https://f-droid.org/packages/com.termux/)，打开 Termux，依次执行：

```bash
pkg update && pkg install git proot-distro
git clone https://github.com/naokisano123-alt/mojing.git ~/mojing
bash ~/mojing/scripts/android-local.sh install
```

安装会下载 Debian 用户空间、前后端依赖并构建网页。需要稳定的网络和足够的手机存储。安装成功后会直接启动，并在 Termux 显示本机登录信息。

1. 保留 Termux 会话，在同一部手机的浏览器打开 [http://127.0.0.1:8795](http://127.0.0.1:8795)。
2. 浏览器弹出登录框时，用户名填 `player`，密码填 Termux 显示的随机本机密码。
3. 新建世界，点「连接模型 API」，填写模型服务的 API 根地址、模型名称和 API Key，再点「测试连接」。返回后创建真实模型旅程。也可先用免 Key 的模拟旅程验收。

本机登录密码在首次启动时生成，会在同一安装中保留。模型 API Key 只暂存于本机引擎的内存会话，结束进程后需要重新连接；模型服务会收到当前回合需要的设定、近期叙事和行动。

## 下次游玩或更新

打开 Termux，执行：

```bash
bash ~/mojing/scripts/android-local.sh start
```

回浏览器打开同一地址。引擎只监听本机地址，其他手机或电脑不能通过这个地址访问。如果浏览器报连不上，返回 Termux 确认进程仍在运行；安卓可能暂停或结束后台会话。重新启动后可以继续已确认的旅程。结束时在 Termux 按 Ctrl+C。

更新版本时，先结束当前运行，再执行：

```bash
git -C ~/mojing pull --ff-only
bash ~/mojing/scripts/android-local.sh install
```

SQLite 存档位于 Debian 环境的 `/root/.local/share/mojing/mojing.db`，安装和更新代码会保留它。删除 Termux 应用数据或 `mojing` Debian 环境也会删除这个文件。
