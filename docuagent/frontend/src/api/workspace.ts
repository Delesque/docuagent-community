import type { UiState, WorkspaceInfo, ProviderConfig, ProviderTestResult } from "./types";
import { get, post } from "./shared";

export async function inspectWorkspace(path: string): Promise<WorkspaceInfo> {
  return get(`/api/workspace?path=${encodeURIComponent(path)}`);
}

export async function chooseFolder(
  initialPath: string,
): Promise<{ cancelled: boolean; workspace?: WorkspaceInfo }> {
  const desktopApi = (
    window as unknown as {
      docuagentDesktop?: {
        chooseFolder: (
          initialPath: string,
        ) => Promise<{ cancelled: boolean; path?: string }>;
      };
    }
  ).docuagentDesktop;
  if (desktopApi?.chooseFolder) {
    const selected = await desktopApi.chooseFolder(initialPath);
    if (selected.cancelled || !selected.path) return { cancelled: true };
    const workspace = await inspectWorkspace(selected.path);
    return { cancelled: false, workspace };
  }
  return post("/api/dialog/folder", { initial_path: initialPath });
}

/** Layout state is persisted separately from architecture, so dragging a node never
 *  bumps architecture_version. Debounced by the caller. */

export async function saveUiState(path: string, uiState: UiState): Promise<UiState> {
  return post("/api/ui-state", { path, ui_state: uiState });
}

// Provider configuration.
//
// IMPORTANT: every backend route reads the provider from a nested `provider` key and
// the project path from `path` (not `project_path`). Flattening the config into the
// request body makes `ProviderConfig.from_payload` return None, which surfaces as
// "请先启用并填写模型连接信息。" even when the form is fully filled in.

export async function testProvider(provider: ProviderConfig): Promise<ProviderTestResult> {
  return post("/api/provider/test", { provider });
}

/** Model listing is proxied through the local server: a browser fetch to the vendor
 *  would be CORS-blocked and would put the key on a cross-origin request. */

export async function listModels(
  provider: ProviderConfig,
): Promise<{ models: string[]; count: number; note?: string }> {
  return post("/api/provider/models", { provider });
}

/** Provider-level reachability only: the endpoint answers and the key is accepted.
 *  Does not require a model name and says nothing about a specific model. */

export async function testProviderReachable(
  provider: ProviderConfig,
): Promise<{ reachable: boolean; base_url: string; model_count: number; note?: string }> {
  return post("/api/provider/reachable", { provider });
}

/** Load the saved provider config from ~/.docuagent/config.json via the local server. */

export async function getProviderConfig(): Promise<ProviderConfig> {
  const config = await get<ProviderConfig>("/api/config");
  const hasApiKey = Boolean(config.has_api_key || config.api_key);
  return {
    ...config,
    has_api_key: hasApiKey,
    api_key: hasApiKey ? "" : (config.api_key || ""),
  };
}

/** Persist the provider config (including api_key) to ~/.docuagent/config.json. */

export async function saveProviderConfig(
  config: ProviderConfig & { clear_api_key?: boolean },
): Promise<void> {
  await post("/api/config", config);
}

// Bootstrap flow