@echo off
chcp 65001 >nul
rem ============================================================
rem AI错题本 Windows 一键打包（对齐 build_mac.sh，R4）
rem 产物：dist\AI错题本-v<版本>-win64.zip
rem 免解压实测：dist\AI错题本\ 就是解压后的最终目录，构建完可直接运行测试
rem 用法：packaging\build_windows.bat          （一键构建）
rem       packaging\build_windows.bat run      （构建完直接启动 exe 冒烟）
rem 前置：Python 3.11+（勾选 Add python.exe to PATH）；Windows 10+（自带 tar）
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set PY=".venv\Scripts\python.exe"

echo [1/6] 准备 venv（首次运行自动创建并装依赖）
if not exist %PY% (
  where py >nul 2>nul && (set PYCMD=py) || set PYCMD=python
  !PYCMD! -m venv .venv
  if errorlevel 1 ( echo [X] 创建 venv 失败：需要 Python 3.11+，安装时勾选 Add python.exe to PATH & exit /b 1 )
  %PY% -m pip install -q -r requirements.txt pyinstaller
  if errorlevel 1 ( echo [X] 依赖安装失败，请检查网络后重试 & exit /b 1 )
)

echo [2/6] 门禁：全量回归必须全绿
%PY% -m pytest tests -q
if errorlevel 1 ( echo [X] 回归未全绿，停止打包 & exit /b 1 )

for /f "delims=" %%v in ('%PY% -c "import paths; print(paths.APP_VERSION)"') do set VER=%%v
echo 版本：v%VER%

echo [3/6] PyInstaller 构建（清理旧产物）
if exist "dist\AI错题本" rmdir /s /q "dist\AI错题本"
%PY% -m PyInstaller packaging\wrongbook.spec --noconfirm --clean
if errorlevel 1 ( echo [X] PyInstaller 构建失败 & exit /b 1 )
if not exist "dist\AI错题本\AI错题本.exe" (
  echo [X] 构建产物缺失：dist\AI错题本\AI错题本.exe
  exit /b 1
)

echo [4/6] 复制静态资源（程序目录布局：static/ engine/ data\kb 种子）
xcopy /E /I /Y static "dist\AI错题本\static\" >nul || ( echo [X] 复制 static 失败 & exit /b 1 )
xcopy /E /I /Y engine "dist\AI错题本\engine\" >nul || ( echo [X] 复制 engine 失败 & exit /b 1 )
xcopy /E /I /Y data\kb "dist\AI错题本\data\kb\" >nul || ( echo [X] 复制 data\kb 失败 & exit /b 1 )

echo [5/6] 白名单校验（本地数据/密钥绝不入包）
set LEAK=
for /r "dist\AI错题本" %%f in (ai_config.json key.env wrongbook.json drafts.json users.json topics.json license*.pem *geogebra*) do set LEAK=%%f
if defined LEAK (
  echo [X] 包内发现敏感文件：!LEAK!
  exit /b 1
)
echo     干净 ✓

echo [6/6] 压 zip（Windows 10+ 自带 tar）
pushd dist
if exist "AI错题本-v%VER%-win64.zip" del "AI错题本-v%VER%-win64.zip"
tar -a -c -f "AI错题本-v%VER%-win64.zip" AI错题本
if errorlevel 1 ( popd & echo [X] 压缩失败 & exit /b 1 )
popd

echo.
echo 完成：dist\AI错题本-v%VER%-win64.zip
echo 免解压实测：dist\AI错题本\ 就是解压后的最终目录，直接运行：
echo     dist\AI错题本\AI错题本.exe
echo 预期：自动开浏览器进入激活页（数据目录 %%APPDATA%%\WrongBook\，升级覆盖不丢数据）
echo 首次运行：SmartScreen 点"更多信息-仍要运行"；防火墙弹窗点"允许"（服务仅绑 127.0.0.1）
if /i "%1"=="run" (
  echo 启动 exe 冒烟...
  start "" "dist\AI错题本\AI错题本.exe"
)
echo.
pause
