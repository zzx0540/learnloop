# 学习环 LearnLoop

多用户 AI 学习平台，包含文档知识库、Wiki、学习计划、间隔复习、测评、知识图谱、英语阅读与写作、课程市场。

后端使用 FastAPI、SQLAlchemy、PostgreSQL；前端使用 React、TypeScript、Vite。模型调用由 AgentRuntime 统一管理。

## 本地启动

需要 Docker 和 Docker Compose。以下步骤只用于新建的独立环境。

1. 将 `.env.example` 复制为 `.env`，替换数据库密码、管理员密码和邀请码。
2. 填写模型服务 API 地址、密钥与实际可用的 `MODEL_LOW`、`MODEL_MEDIUM`、`MODEL_HIGH`；视觉模型和 MinerU 按需配置。示例不包含可用凭据，AI 功能需要自行配置服务。
3. 执行 `docker compose up -d --build`。
4. 打开 `http://localhost:8080`，使用自行配置的管理员账号登录。
5. 执行 `curl http://localhost:8080/api/health` 检查服务状态。

首次启动执行 Alembic 迁移。数据库与上传文件分别保存在 `pgdata` 和 `files` 卷中；不要对有数据的实例执行 `docker compose down -v`。

## 部署

`deploy/` 为可选运维示例，不在启动时自动执行。`sync.sh` 必须显式设置 `LEARNLOOP_SERVER`，且使用 `rsync --delete`：仅对自己新建的专用部署目录使用。默认远端目录为 `/opt/learnloop`。

Nginx 示例域名为 `learn.example.com`，证书路径需要按自己的域名配置。公网部署应配置 HTTPS，并设置 `COOKIE_SECURE=true`。不要使用示例密码部署。

`backup.sh` 仅备份数据库，并删除超过14天的匹配备份；不会备份上传文件。使用前自行确认路径、保留策略，并另行备份 `files` 卷。

## 开发与验证

- `backend/`：API、业务逻辑、Alembic 迁移、测试。
- `frontend/`：界面与前端构建配置。
- 前端构建：在 `frontend` 执行 `npm ci` 后执行 `npm run build`。
- 后端测试：使用独立 PostgreSQL 测试库和隔离环境；先检查 `backend/tests/conftest.py` 的数据库初始化逻辑，勿对生产数据库运行测试。

本候选副本仅做公开内容整理与静态检查，未重新执行完整构建和业务回归；历史生产验收不代表本副本已经通过测试。

## 公开范围与许可

本副本不包含生产配置、用户数据、数据库备份、运维记录、截图及原仓库 Git 历史。部署地址已替换为示例。

项目许可证尚待作者确定；当前不声明任何开源许可。依赖保留其各自许可证，正式发布前还需核查依赖许可兼容性。
