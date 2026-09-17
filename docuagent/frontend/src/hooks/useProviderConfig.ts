import { useCallback, useEffect, useState } from "react";
import { getProviderConfig, saveProviderConfig } from "../api";
import { EMPTY_PROVIDER } from "../conversation/workbenchConfig";
import type { ProviderConfig } from "../api";

export function isProviderConfigured(provider: ProviderConfig): boolean {
  return Boolean(
    provider.enabled &&
      provider.base_url &&
      provider.model &&
      (provider.api_key || provider.has_api_key),
  );
}

export function useProviderConfig(): {
  provider: ProviderConfig;
  configured: boolean;
  save: (next: ProviderConfig) => void;
} {
  const [provider, setProvider] = useState<ProviderConfig>(EMPTY_PROVIDER);

  useEffect(() => {
    let cancelled = false;
    getProviderConfig()
      .then((saved) => {
        if (!cancelled) setProvider(saved);
      })
      .catch(() => {
        // The settings dialog remains usable when the backend is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback((next: ProviderConfig) => {
    setProvider(next);
    void saveProviderConfig(next).catch(() => {
      // Persistence is best effort; the in-memory setting remains active.
    });
  }, []);

  return { provider, configured: isProviderConfigured(provider), save };
}
