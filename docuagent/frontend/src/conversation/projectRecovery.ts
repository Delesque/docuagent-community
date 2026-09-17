export function resolveInitialProjectPath(
  search: string,
  savedPath: string | null,
): string | null {
  const query = new URLSearchParams(search).get("path")?.trim();
  return query || savedPath || null;
}

export function projectReloadUrl(currentUrl: string, path: string): string {
  const url = new URL(currentUrl);
  url.searchParams.set("path", path);
  return url.toString();
}
