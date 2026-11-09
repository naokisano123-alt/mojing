# 墨境 Android 应用

Android 7.0 及以上的 64 位 ARM 设备可安装预览 APK。应用启动后直接进入墨境，世界资料和冒险存档都保存在应用自己的目录。

1. 在首页新建世界，进入「冒险记录」。
2. 点「连接模型 API」，填写服务商的 HTTPS API 根地址、准确的模型名和 API Key，再点「测试连接」。
3. 返回并新建真实模型旅程。写行动、确认回合后，进度会自动保存。也可以先创建免 Key 的模拟旅程。Word 导入取决于设备能否加载解析组件；缺少时可将文档另存为 TXT 或 Markdown。

模型 API Key 由 Android Keystore 保护并保存在本机，重开应用会自动恢复。可在模型设置中选择「删除本机密钥」。已确认的进度保存在本机。卸载或清除应用数据会删除本机存档。预览 APK 使用调试签名，不同签名的包无法直接覆盖安装。

不同手机的世界、存档和模型配置彼此独立。应用配置为从 Android 自动备份和设备间自动迁移中排除本地数据。共用同一手机上的同一个安装时，使用的是同一份资料库。世界文件由玩家主动导出，包含玩家勾选的数据。以后实现导入时，应由玩家明确选择文件，并默认在当前设备中创建独立世界，分配新的本地标识。

## 构建

需要 JDK 17 或 21、Gradle 8.11.1、Android SDK 35（含 Build Tools 35.0.0）、Python 3.12、Node.js 22。从仓库根目录运行：

```bash
npm ci
npm run build
gradle -p android :app:assembleDebug
```

如需并排安装旧版，可在构建命令末尾添加 `-PmojingSideBySide`，安装后的应用名称为「墨境试玩版」，使用独立存档。后续覆盖安装同一预览包须用同一签名，可用 `MOJING_PREVIEW_KEYSTORE` 指定保存在仓库外的调试签名文件。

APK 输出于 `android/app/build/outputs/apk/debug/app-debug.apk`。应用复用 `server/app` 中的冒险和资料库代码，前端构建产物与 Python 运行时都包含在 APK 里。应用内引擎只接受本机回环访问，WebView 自动认证，外部模型地址需使用 HTTPS。Android 构建使用独立的 Python 依赖清单，电脑开发和云端部署沿用原依赖。
