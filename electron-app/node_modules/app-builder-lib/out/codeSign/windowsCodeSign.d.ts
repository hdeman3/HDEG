import { WindowsConfiguration } from "../options/winOptions";
import { VmManager } from "../vm/vm";
import { WinPackager } from "../winPackager";
export interface WindowsSignOptions {
    readonly path: string;
    readonly options: WindowsConfiguration;
}
export declare function signWindows(options: WindowsSignOptions, packager: WinPackager): Promise<boolean>;
export declare function getPSCmd(vm: VmManager): Promise<string>;
