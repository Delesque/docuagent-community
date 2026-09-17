const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const BACKEND_PORT = 8765;
const APP_ROOT = app.isPackaged
  ? path.join(process.resourcesPath, "app")
  : path.resolve(__dirname, "..");
const BACKEND_ENTRY = path.join(APP_ROOT, "docuagent.py");
const FRONTEND_ENTRY = path.join(APP_ROOT, "web-next", "index.html");
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// The packaged app ships its own interpreter so the desktop build does not depend on
// a Python installation on the user's PATH. `python-runtime` sits next to `app` in the
// packaged resources; in the development tree the same runtime is prepared by
// `embed_python.py` in the desktop build directory.
const EMBEDDED_PYTHON_CANDIDATES = app.isPackaged
  ? [
      path.join(process.resourcesPath, "python-runtime", "python.exe"),
      path.join(process.resourcesPath, "python-runtime", "bin", "python3"),
    ]
  : [
      path.join(__dirname, "build", "python-runtime", "python.exe"),
      path.resolve(__dirname, "..", "..", ".release", "runtime-embed", "python-3.12.10", "python.exe"),
    ];

let backendProcess = null;
let mainWindow = null;
let quitting = false;

function embeddedPython() {
  for (const candidate of EMBEDDED_PYTHON_CANDIDATES) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function pythonCommand() {
  const embedded = embeddedPython();
  if (embedded) return embedded;
  return process.platform === "win32" ? "python" : "python3";
}

function backendHealthy() {
  return new Promise((resolve) => {
    const request = http.get(
      `${BACKEND_URL}/api/health`,
      (response) => {
        response.resume();
        resolve(response.statusCode === 200);
      },
    );
    request.setTimeout(1500, () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

function waitForBackend(deadline = Date.now() + 25_000) {
  return new Promise((resolve, reject) => {
    const check = async () => {
      if (quitting) {
        reject(new Error("应用正在退出"));
        return;
      }
      if (await backendHealthy()) {
        resolve();
        return;
      }
      if (Date.now() >= deadline) {
        reject(new Error("后端启动超时"));
        return;
      }
      setTimeout(check, 500);
    };
    void check();
  });
}

async function ensureBackend() {
  if (await backendHealthy()) return;

  backendProcess = spawn(
    pythonCommand(),
    [
      BACKEND_ENTRY,
      "--host",
      "127.0.0.1",
      "--port",
      String(BACKEND_PORT),
      "--no-browser",
    ],
    {
      cwd: APP_ROOT,
      stdio: "ignore",
      windowsHide: true,
    },
  );

  backendProcess.on("error", (error) => {
    dialog.showErrorBox(
      "DocuAgent 后端启动失败",
      `无法启动打包内的 Python 运行时：${error.message}\n\n` +
        `期望路径：${EMBEDDED_PYTHON_CANDIDATES.join(" 或 ")}\n\n` +
        "该文件随安装包提供；若缺失，请重新安装或从源码运行 `DocuAgent-Desktop.bat`。",
    );
    app.quit();
  });

  backendProcess.on("exit", (code) => {
    if (!quitting && mainWindow) {
      dialog.showErrorBox(
        "DocuAgent 后端已退出",
        `Python 后端进程已退出，退出码 ${code ?? "未知"}。`,
      );
      app.quit();
    }
  });

  await waitForBackend();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 680,
    title: "DocuAgent",
    autoHideMenuBar: true,
    backgroundColor: "#0B100B",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.cjs"),
    },
  });

  mainWindow.loadURL(BACKEND_URL);
  if (process.env.DOCUAGENT_DESKTOP_SMOKE === "1") {
    mainWindow.webContents.once("did-finish-load", async () => {
      const hasBridge = await mainWindow.webContents.executeJavaScript(
        "typeof window.docuagentDesktop?.chooseFolder === 'function'",
      );
      if (!hasBridge) {
        console.error("Desktop folder picker bridge is missing");
        app.exit(1);
        return;
      }
      setTimeout(() => app.quit(), 500);
    });
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  ipcMain.handle("choose-folder", async (_event, initialPath) => {
    const options = {
      title: "选择 DocuAgent 项目文件夹",
      properties: ["openDirectory", "createDirectory"],
      ...(initialPath ? { defaultPath: initialPath } : {}),
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options);
    if (result.canceled || result.filePaths.length === 0) {
      return { cancelled: true };
    }
    return { cancelled: false, path: result.filePaths[0] };
  });

  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    if (!fs.existsSync(FRONTEND_ENTRY)) {
      dialog.showErrorBox(
        "DocuAgent 前端未构建",
        `缺少 ${FRONTEND_ENTRY}。请先运行 docuagent\\frontend 下的 npm run build，或使用一键启动脚本。`,
      );
      app.quit();
      return;
    }

    try {
      await ensureBackend();
      createWindow();
    } catch (error) {
      dialog.showErrorBox(
        "DocuAgent 启动失败",
        `后端启动失败：${error instanceof Error ? error.message : String(error)}`,
      );
      app.quit();
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
    if (backendProcess && !backendProcess.killed) backendProcess.kill();
  });
}
