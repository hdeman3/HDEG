import { contextBridge, ipcRenderer } from 'electron';
const api = {
    // 标识运行环境：Electron 桌面端
    isElectron: true,
    config: {
        getAll: () => ipcRenderer.invoke('config:getAll'),
        getSection: (section) => ipcRenderer.invoke('config:getSection', section),
        setSection: (section, data) => ipcRenderer.invoke('config:setSection', section, data),
        listPresets: () => ipcRenderer.invoke('config:listPresets'),
        setActivePreset: (name) => ipcRenderer.invoke('config:setActivePreset', name),
        savePreset: (name) => ipcRenderer.invoke('config:savePreset', name),
        deletePreset: (name) => ipcRenderer.invoke('config:deletePreset', name),
    },
    dialog: {
        selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
    },
    folder: {
        scanWorks: (dirPath) => ipcRenderer.invoke('folder:scanWorks', dirPath),
    },
    translate: {
        start: (workDir, workId) => ipcRenderer.invoke('translate:start', workDir, workId),
        cancel: () => ipcRenderer.invoke('translate:cancel'),
    },
    aid: {
        read: (workDir) => ipcRenderer.invoke('aid:read', workDir),
        save: (workDir, type, data) => ipcRenderer.invoke('aid:save', workDir, type, data),
    },
    review: {
        fetchResults: (workDir) => ipcRenderer.invoke('review:fetchResults', workDir),
        saveEdit: (workDir, filename, index, newTranslation) => ipcRenderer.invoke('review:saveEdit', workDir, filename, index, newTranslation),
        consistencyCheck: (workDir) => ipcRenderer.invoke('review:consistencyCheck', workDir),
    },
    utils: {
        openFolder: (dirPath) => ipcRenderer.invoke('utils:openFolder', dirPath),
        getProjectRoot: () => ipcRenderer.invoke('utils:getProjectRoot'),
    },
    on: (channel, callback) => {
        const validChannels = ['python:message', 'python:log', 'python:done', 'python:error'];
        if (validChannels.includes(channel)) {
            const subscription = (_event, ...args) => callback(...args);
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const subs = ipcRenderer.__subscriptions ??= {};
            subs[channel] = subscription;
            ipcRenderer.on(channel, subscription);
        }
    },
    off: (channel, _callback) => {
        const validChannels = ['python:message', 'python:log', 'python:done', 'python:error'];
        if (validChannels.includes(channel)) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const subscription = ipcRenderer.__subscriptions?.[channel];
            if (subscription) {
                ipcRenderer.removeListener(channel, subscription);
            }
        }
    },
};
contextBridge.exposeInMainWorld('electronAPI', api);
//# sourceMappingURL=index.js.map