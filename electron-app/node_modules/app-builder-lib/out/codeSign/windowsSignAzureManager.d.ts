import { WinPackager } from "../winPackager";
import { WindowsSignOptions } from "./windowsCodeSign";
export declare class WindowsSignAzureManager {
    private readonly packager;
    constructor(packager: WinPackager);
    initializeProviderModules(): Promise<void>;
    verifyRequiredEnvVars(): void;
    verifyPrincipleSecretEnv(): boolean;
    verifyPrincipleCertificateEnv(): boolean;
    verifyUsernamePasswordEnv(): boolean;
    signUsingAzureTrustedSigning(options: WindowsSignOptions): Promise<boolean>;
}
