import { describe, expect, it } from "vitest";
import { registryZoomLevel, resolveRegistryLevel } from "./RegistryOverlay";

describe("registryZoomLevel", () => {
  it("maps the scale bands to the four semantic levels", () => {
    expect(registryZoomLevel(0.3)).toBe("cluster");
    expect(registryZoomLevel(0.6)).toBe("cluster");
    expect(registryZoomLevel(0.61)).toBe("row");
    expect(registryZoomLevel(1.0)).toBe("row");
    expect(registryZoomLevel(1.41)).toBe("field");
    expect(registryZoomLevel(1.5)).toBe("field");
    expect(registryZoomLevel(1.9)).toBe("symbol");
  });
});

describe("resolveRegistryLevel", () => {
  it("collapsed always forces the cluster level regardless of zoom", () => {
    for (const scale of [0.3, 0.6, 1.0, 1.5, 2.0]) {
      expect(resolveRegistryLevel(false, scale)).toBe("cluster");
    }
  });

  it("expanded never drops below row even when zoomed out", () => {
    expect(resolveRegistryLevel(true, 0.3)).toBe("row");
    expect(resolveRegistryLevel(true, 0.6)).toBe("row");
  });

  it("expanded refines row -> field -> symbol as you zoom in", () => {
    expect(resolveRegistryLevel(true, 0.8)).toBe("row");
    expect(resolveRegistryLevel(true, 1.2)).toBe("field");
    expect(resolveRegistryLevel(true, 1.8)).toBe("symbol");
  });
});
