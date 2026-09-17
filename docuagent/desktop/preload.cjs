const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("docuagentDesktop", {
  chooseFolder: (initialPath) =>
    ipcRenderer.invoke("choose-folder", initialPath),
});
