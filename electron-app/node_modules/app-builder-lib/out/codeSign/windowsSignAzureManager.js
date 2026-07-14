"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.WindowsSignAzureManager = void 0;
const builder_util_1 = require("builder-util");
const windowsCodeSign_1 = require("./windowsCodeSign");
class WindowsSignAzureManager {
    constructor(packager) {
        this.packager = packager;
    }
    async initializeProviderModules() {
        const vm = await this.packager.vm.value;
        const ps = await (0, windowsCodeSign_1.getPSCmd)(vm);
        builder_util_1.log.info(null, "installing required module (TrustedSigning) with scope CurrentUser");
        try {
            await vm.exec(ps, ["-NoProfile", "-NonInteractive", "-Command", "Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser"]);
        }
        catch (error) {
            // Might not be needed, seems GH runners already have NuGet set up.
            // Logging to debug just in case users run into this. If NuGet isn't present, Install-Module -Name TrustedSigning will fail, so we'll get the logs at that point
            builder_util_1.log.debug({ message: error.message || error.stack }, "unable to install PackageProvider Nuget. Might be a false alarm though as some systems already have it installed");
        }
        await vm.exec(ps, ["-NoProfile", "-NonInteractive", "-Command", "Install-Module -Name TrustedSigning -RequiredVersion 0.4.1 -Force -Repository PSGallery -Scope CurrentUser"]);
        // Preemptively check env vars once during initialization
        // Options: https://learn.microsoft.com/en-us/dotnet/api/azure.identity.environmentcredential?view=azure-dotnet#definition
        builder_util_1.log.info(null, "verifying env vars for authenticating to Microsoft Entra ID");
        this.verifyRequiredEnvVars();
        if (!(this.verifyPrincipleSecretEnv() || this.verifyPrincipleCertificateEnv() || this.verifyUsernamePasswordEnv())) {
            throw new builder_util_1.InvalidConfigurationError(`Unable to find valid azure env configuration for signing. Missing field(s) can be debugged via "DEBUG=electron-builder". Please refer to: https://learn.microsoft.com/en-us/dotnet/api/azure.identity.environmentcredential?view=azure-dotnet#definition`);
        }
    }
    verifyRequiredEnvVars() {
        ;
        ["AZURE_TENANT_ID", "AZURE_CLIENT_ID"].forEach(field => {
            if (!process.env[field]) {
                throw new builder_util_1.InvalidConfigurationError(`Unable to find valid azure env field ${field} for signing. Please refer to: https://learn.microsoft.com/en-us/dotnet/api/azure.identity.environmentcredential?view=azure-dotnet#definition`);
            }
        });
    }
    verifyPrincipleSecretEnv() {
        if (!process.env.AZURE_CLIENT_SECRET) {
            builder_util_1.log.debug({ envVar: "AZURE_CLIENT_SECRET" }, "no secret found for authenticating to Microsoft Entra ID");
            return false;
        }
        return true;
    }
    verifyPrincipleCertificateEnv() {
        if (!process.env.AZURE_CLIENT_CERTIFICATE_PATH) {
            builder_util_1.log.debug({ envVar: "AZURE_CLIENT_CERTIFICATE_PATH" }, "no path found for signing certificate for authenticating to Microsoft Entra ID");
            return false;
        }
        if (!process.env.AZURE_CLIENT_CERTIFICATE_PASSWORD) {
            builder_util_1.log.debug({ envVar: "AZURE_CLIENT_CERTIFICATE_PASSWORD" }, "(optional) certificate password not found, assuming no password");
        }
        if (!process.env.AZURE_CLIENT_SEND_CERTIFICATE_CHAIN) {
            builder_util_1.log.debug({ envVar: "AZURE_CLIENT_SEND_CERTIFICATE_CHAIN" }, "(optional) certificate chain not found");
        }
        return true;
    }
    verifyUsernamePasswordEnv() {
        if (!process.env.AZURE_USERNAME) {
            builder_util_1.log.debug({ envVar: "AZURE_USERNAME" }, "no username found for authenticating to Microsoft Entra ID");
            if (!process.env.AZURE_PASSWORD) {
                builder_util_1.log.debug({ envVar: "AZURE_PASSWORD" }, "no password found for authenticating to Microsoft Entra ID");
            }
            return false;
        }
        return true;
    }
    // prerequisite: requires `initializeProviderModules` to already have been executed
    async signUsingAzureTrustedSigning(options) {
        const vm = await this.packager.vm.value;
        const ps = await (0, windowsCodeSign_1.getPSCmd)(vm);
        const { endpoint, certificateProfileName, codeSigningAccountName, ...extraSigningArgs } = options.options.azureSignOptions;
        const params = {
            FileDigest: "SHA256",
            ...extraSigningArgs, // allows overriding FileDigest if provided in config
            Endpoint: endpoint,
            CertificateProfileName: certificateProfileName,
            CodeSigningAccountName: codeSigningAccountName,
            Files: options.path,
        };
        const paramsString = Object.entries(params)
            .reduce((res, [field, value]) => {
            return [...res, `-${field}`, value];
        }, [])
            .join(" ");
        await vm.exec(ps, ["-NoProfile", "-NonInteractive", "-Command", `Invoke-TrustedSigning ${paramsString}`]);
        return true;
    }
}
exports.WindowsSignAzureManager = WindowsSignAzureManager;
//# sourceMappingURL=windowsSignAzureManager.js.map