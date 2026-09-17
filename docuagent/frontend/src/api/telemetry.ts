import type { TelemetrySummary } from "./types";
import { get, post } from "./shared";

export async function fetchTelemetry(): Promise<TelemetrySummary> {
  return get("/api/telemetry");
}

export async function setTelemetryConsent(
  consent: boolean,
): Promise<TelemetrySummary> {
  return post("/api/telemetry/config", { consent });
}

export async function uploadTelemetry(): Promise<{
  uploaded: boolean;
  reason?: string;
  uploaded_at?: string;
}> {
  return post("/api/telemetry/upload", {});
}

export async function clearTelemetry(): Promise<TelemetrySummary> {
  return post("/api/telemetry/clear", {});
}
