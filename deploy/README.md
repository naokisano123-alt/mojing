# 墨境手机联网试玩部署

这套配置供个人私有试玩使用。它把网页和 API 放在同一 HTTPS 地址，浏览器用同一套站点凭据访问，数据库保存在服务器的数据卷里。站点登录密码与模型 API Key 是两种不同的凭据。多个使用同一登录密码的设备共享同一资料库和存档。

1. 准备能从手机访问的服务器和域名，安装 Docker 与 Compose，给域名配置 DNS 并开放 TCP 80 和 443。克隆本仓库并进入仓库根目录。
2. 运行 `docker run --rm -it caddy:2-alpine caddy hash-password`，按提示输入一个长且随机的站点密码。将输出的哈希填入下方配置，保留外层单引号。
3. 在仓库根目录新建 `.env`，用自己的域名和受信任的模型服务地址替换示例。只添加你信任且由公开模型供应商控制的 HTTPS 地址，多个地址用逗号分隔。可先留空，仅使用免 Key 的模拟旅程。

   ```dotenv
   MOJING_PUBLIC_ORIGIN=https://adventure.example.com
   MOJING_PASSWORD_HASH='这里填生成的哈希'
   MOJING_ALLOWED_MODEL_BASE_URLS=https://api.openai.com/v1
   ```

4. 运行 `docker compose up -d --build`。若无法访问，运行 `docker compose logs web api` 检查证书和后端启动情况。
5. 在安卓浏览器打开配置的 HTTPS 地址，用户名输入 `player`，密码输入第 2 步设置的原始站点密码。进入或创建世界，点击「连接模型 API」，填写白名单中对应的 API 根地址、模型名和自己的 Key。可以先「测试连接」，然后返回旅程。创建真实模型旅程、发送一次行动并确认，刷新页面检查记录和存档。

模型 API Key 通过 HTTPS 提交，只暂存服务端内存会话，闲置 30 分钟后失效。页面刷新后需重新连接模型，已确认的进度仍保存在 SQLite。模型调用会向所选服务发送与当前行动有关的世界资料和近期剧情。

应用容器只通过 Compose 内部网络提供 API。配置保持单实例和单 worker，模型会话在这一进程内管理。请备份 `game_data` 数据卷；更新容器时保留该卷。如果迁移本地资料库，先停止写入，再用 SQLite 的备份功能复制数据库。站点只供你或信任的人使用。
