# 打包桌面安装包流程

把前后端整体打包成 Windows 桌面安装包(`Setup.exe`)。产物是 pywebview 窗口壳 + 本地 uvicorn 服务,
双击安装即可使用,无需 Docker / 公网部署。

## 产物一览

| 产物 | 路径 |
|---|---|
| PyInstaller 打包目录 | `backend/dist/StrategyMind/` |
| **最终安装包** | `packaging/Output/StrategyMind-Setup-<版本>.exe` |

版本号取自 `frontend/package.json`(当前 `0.1.88`)。

---

## 前置条件

- **Node + pnpm**(构建前端)
- **uv**(Python 依赖管理;`uv sync` 会把依赖装进 `backend/.venv`,与 conda 互不干扰)
- **Inno Setup 6**(`ISCC.exe`,生成安装包用)
- 本项目后端依赖 + PyInstaller 已装入 venv(见步骤 2、3,只需做一次)

> **没有管理员权限时装 Inno Setup**:官方安装器支持静默装到用户目录,
> 已在本机装好便携版:`C:\Users\dev\innosetup-2\ISCC.exe`(免管理员,直接用)。
> 后续打包命令里的 `ISCC` 都指向它即可。

---

## 快速打包(改完代码后的一条龙)

在项目根目录依次执行:

```powershell
# 1. 构建前端 (tsc + vite build → frontend/dist)
cd frontend
pnpm build

# 2. 同步后端依赖(首次打包才需要;desktop=pywebview, legacy-cpu=老 CPU 兼容内核)
cd ..\backend
uv sync --no-dev --extra desktop --extra legacy-cpu

# 3. 装 PyInstaller(首次打包才需要)
#    ⚠️ 必须加 --python .venv:shell 在 conda base 里时,uv pip 默认会装进 conda,无写权限直接报错
uv pip install --python .venv pyinstaller

# 4. PyInstaller 打包 → backend/dist/StrategyMind/
uv run pyinstaller ../packaging/strategymind.spec --noconfirm

# 5. Inno Setup 生成安装包 → packaging/Output/StrategyMind-Setup-<版本>.exe
cd ..
& "$env:USERPROFILE\innosetup-2\ISCC.exe" /DMyAppVersion=0.1.88 packaging\strategymind.iss
```

最后一步的 `MyAppVersion` 记得与 `frontend/package.json` 里的版本对齐。

---

## 分步详解

### 1. 构建前端

```powershell
cd frontend
pnpm build        # 首次需先 pnpm install
```

- 产出 `frontend/dist/`(index.html + 静态资源)。
- 会被 PyInstaller 整体打进 `_internal/static/`,frozen 模式下 `config.py` 从这里读。
- 若 `tsc -b` 报类型错误会中断,先修前端。

### 2. 同步后端依赖

```powershell
cd backend
uv sync --no-dev --extra desktop --extra legacy-cpu
```

- `--extra desktop`:装 pywebview(桌面窗口壳),`desktop.py` import 需要它。
- `--extra legacy-cpu`:装 polars `rtcompat` 兼容内核,让安装包同时支持有/无 AVX2 的 CPU。
  `strategymind.spec` 会按 `find_spec` 自动收集对应运行时。
- `--no-dev`:跳过 pytest/ruff/mypy 等开发依赖,减小包体积。
- 不用 `--extra backtest`(vectorbt 回测链)会显式被 spec exclude。

### 3. 安装 PyInstaller

```powershell
uv pip install --python .venv pyinstaller
```

**⚠️ 坑**:shell 若在 conda `base` 环境里,`uv pip install` 默认把包装进
`C:\ProgramData\miniconda3`(无写权限 → `拒绝访问` 报错)。必须 `--python .venv`
显式指到项目 venv(装在 `backend/.venv` 下,与 `uv sync` 一致)。

### 4. PyInstaller 打包

```powershell
cd backend
uv run pyinstaller ../packaging/strategymind.spec --noconfirm
```

- 在 `backend` 目录执行(用该目录的 venv);spec 里 `SPECPATH` 自动定位到项目根,
  所以 `frontend/dist`、`tiers.yaml` 等相对路径都正确。
- 产出 `backend/dist/StrategyMind/`(onedir 模式,启动快、调试友好)。
- 关键配置(spec 内):
  - `collect_all` polars/pyarrow/duckdb/fastexcel + `_polars_runtime_32/_compat`(原生库 `.libs/` 必须完整,否则启动崩);
  - hidden imports:pywebview、winotify/plyer 平台后端、uvicorn 动态加载模块、fastapi/pydantic/tickflow 元数据(`copy_metadata`,否则 frozen 后报 `PackageNotFoundError`);
  - `console=False`(桌面应用不弹黑窗,`desktop.py` 里有 `_guard_streams` 守护 stdout/stderr);
  - `excludes`:vectorbt/numba/llvmlite/matplotlib 等重型依赖,控制体积;
  - macOS 自动加 `BUNDLE` 产出 `.app`(Windows/Linux 用目录即最终产物)。

### 5. Inno Setup 生成安装包

```powershell
& "$env:USERPROFILE\innosetup-2\ISCC.exe" /DMyAppVersion=0.1.88 packaging\strategymind.iss
```

- `strategymind.iss` 把 `backend/dist/StrategyMind/` 整个目录封进一个 `Setup.exe`。
- 设计要点(iss 内注释详述):
  - 免管理员:装到 `D:\` 或用户目录(`{localappdata}\Programs\`,无 D 盘自动回退);
  - 用户数据在安装目录 `data/`,卸载时询问是否保留(覆盖安装不丢数据);
  - 简中 + 英文双语安装向导,中文语言包已随仓库内置。
- 产出 `packaging/Output/StrategyMind-Setup-0.1.88.exe`(LZMA2 压缩,约 118 MB,耗时约 2 分钟)。

---

## 验证安装包(推荐)

打包完至少做一次冒烟测试——静默装到临时目录,启动成品 exe,查健康接口:

```powershell
$tmp = "$env:TEMP\tickflow-smoke"
Start-Process "packaging\Output\StrategyMind-Setup-0.1.88.exe" `
  -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-","/DIR=$tmp",'/TASKS=""' -Wait
# 启动后它自动选 3018 起第一个空闲端口(desktop.py _find_free_port)
Start-Process "$tmp\StrategyMind.exe"
# 等几秒后:
curl.exe -s http://127.0.0.1:3018/health
# 期望: {"status":"ok","version":"0.1.88",...}
```

正常表现:首启会拉取一年的日 K 并全量算指标(几分钟),`data/desktop.log` 记录启动日志,崩溃会弹原生提示框。

---

## 不用本机工具链:走 GitHub Actions

本机没装 Inno Setup / 不想配环境时,直接用仓库自带的 CI:

```bash
gh workflow run release.yml -f version=v0.1.88 -f platforms=windows
```

或 GitHub 网页 → Actions → 桌面客户端发布 → Run workflow(填版本号、平台)。
构建在云端完成,自动建 Release 并上传安装包。CI 与本地流程等价:
`pnpm build` → `uv sync --no-dev --extra desktop --extra legacy-cpu` → PyInstaller → Inno Setup。

---

## 常见问题

| 现象 | 原因 / 解法 |
|---|---|
| `uv sync` 报 `Readme path must be within the project directory` | pyproject 里 `readme` 指向了项目目录外(如 `../README.md`),hatchling 拒绝。删掉该行或改为包内路径。已修复,勿加回 |
| `uv pip install` 报 `拒绝访问`、装进 `C:\ProgramData\miniconda3` | shell 在 conda base,uv 发现到了 conda 环境。加 `--python .venv` 指到项目 venv |
| `ISCC` 找不到 | Inno Setup 未装。无管理员时用便携版:`C:\Users\dev\innosetup-2\ISCC.exe`,或走 GitHub Actions |
| 打包后 exe 双击一闪退出、查无日志 | 看安装目录 `data/desktop.log`(desktop.py 会把日志落盘);多为缺 hidden import / 原生库没收集全 |
| 老 CPU(无 AVX2)报 Polars 加载失败 | 打包时需 `--extra legacy-cpu`,且 spec 里 `_polars_runtime_compat` 必须被收集(CI 有专门检查步骤) |
| 安装包体积大(约 118 MB) | 大头是 Python 运行时 + polars/pyarrow/duckdb 原生库,属正常;onedir + 安装包压缩已是最优 |
