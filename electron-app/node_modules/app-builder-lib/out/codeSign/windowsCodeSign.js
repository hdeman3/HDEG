"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.signWindows = signWindows;
exports.getPSCmd = getPSCmd;
const builder_util_1 = require("builder-util");
async function signWindows(options, packager) {
    if (options.options.azureSignOptions) {
        builder_util_1.log.info({ path: builder_util_1.log.filePath(options.path) }, "signing with Azure Trusted Signing (beta)");
        return (await packager.azureSignManager.value).signUsingAzureTrustedSigning(options);
    }
    builder_util_1.log.info({ path: builder_util_1.log.filePath(options.path) }, "signing with signtool.exe");
    const deprecatedFields = {
        sign: options.options.sign,
        signDlls: options.options.signDlls,
        signingHashAlgorithms: options.options.signingHashAlgorithms,
        certificateFile: options.options.certificateFile,
        certificatePassword: options.options.certificatePassword,
        certificateSha1: options.options.certificateSha1,
        certificateSubjectName: options.options.certificateSubjectName,
        additionalCertificateFile: options.options.additionalCertificateFile,
        rfc3161TimeStampServer: options.options.rfc3161TimeStampServer,
        timeStampServer: options.options.timeStampServer,
        publisherName: options.options.publisherName,
    };
    const fields = Object.entries(deprecatedFields)
        .filter(([, value]) => !!value)
        .map(([field]) => field);
    if (fields.length) {
        builder_util_1.log.warn({ fields, reason: "please move to win.signtoolOptions.<field_name>" }, `deprecated field`);
    }
    return (await packager.signtoolManager.value).signUsingSigntool(options);
}
async function getPSCmd(vm) {
    return await vm
        .exec("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", `Get-Command pwsh.exe`])
        .then(() => {
        builder_util_1.log.debug(null, "identified pwsh.exe for executing code signing");
        return "pwsh.exe";
    })
        .catch(() => {
        builder_util_1.log.debug(null, "unable to find pwsh.exe, falling back to powershell.exe");
        return "powershell.exe";
    });
}
//# sourceMappingURL=windowsCodeSign.js.map