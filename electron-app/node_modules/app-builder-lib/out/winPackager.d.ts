import { Arch, FileTransformer } from "builder-util";
import { Lazy } from "lazy-val";
import { WindowsSignToolManager } from "./codeSign/windowsSignToolManager";
import { AfterPackContext } from "./configuration";
import { Target } from "./core";
import { RequestedExecutionLevel, WindowsConfiguration } from "./options/winOptions";
import { Packager } from "./packager";
import { PlatformPackager } from "./platformPackager";
import { VmManager } from "./vm/vm";
import { WindowsSignAzureManager } from "./codeSign/windowsSignAzureManager";
export declare class WinPackager extends PlatformPackager<WindowsConfiguration> {
    _iconPath: Lazy<string | null>;
    readonly vm: Lazy<VmManager>;
    readonly signtoolManager: Lazy<WindowsSignToolManager>;
    readonly azureSignManager: Lazy<WindowsSignAzureManager>;
    get isForceCodeSigningVerification(): boolean;
    constructor(info: Packager);
    get defaultTarget(): Array<string>;
    createTargets(targets: Array<string>, mapper: (name: string, factory: (outDir: string) => Target) => void): void;
    getIconPath(): Promise<string | null>;
    doGetCscPassword(): string | undefined | null;
    sign(file: string): Promise<boolean>;
    private doSign;
    signAndEditResources(file: string, arch: Arch, outDir: string, internalName?: string | null, requestedExecutionLevel?: RequestedExecutionLevel | null): Promise<void>;
    private shouldSignFile;
    protected createTransformerForExtraFiles(packContext: AfterPackContext): FileTransformer | null;
    protected signApp(packContext: AfterPackContext, isAsar: boolean): Promise<boolean>;
}
