# Third-Party Dependencies

This review applies to the DocuAgent Community source distribution. The export contains application source, lockfiles, documentation, and the application icon. It does not contain `node_modules`, the generated `docuagent/web-next` frontend bundle, an Electron binary, Python packages, archived UI references, or the `ink-grain.png` texture.

## Frontend runtime dependencies

The following packages are downloaded from npm when a user builds the frontend. Their code is not copied into the source export.

| Package | Locked version | License |
|---|---:|---|
| `@monaco-editor/loader` | 1.7.0 | MIT |
| `@monaco-editor/react` | 4.7.0 | MIT |
| `@types/trusted-types` | 2.0.7 | MIT |
| `@xterm/addon-fit` | 0.11.0 | MIT |
| `@xterm/addon-web-links` | 0.12.0 | MIT |
| `@xterm/xterm` | 6.0.0 | MIT |
| `dompurify` | 3.4.15 | MPL-2.0 OR Apache-2.0 |
| `lucide-react` | 1.46.0 | ISC |
| `marked` | 14.0.0 | MIT |
| `monaco-editor` | 0.56.0 | MIT |
| `react` | 19.2.0 | MIT |
| `react-dom` | 19.2.0 | MIT |
| `scheduler` | 0.27.0 | MIT |
| `state-local` | 1.0.7 | MIT |

The exact dependency graph is recorded in `docuagent/frontend/package-lock.json`. Installed-package metadata was checked on 2026-09-16 and contained no package with missing license metadata. Build and test dependencies include Electron, TypeScript, Vite, Vitest, Tailwind CSS, PostCSS, jsdom, axe-core, React type packages, and pytest; they are not included in the source export and remain under their own licenses.

## Distribution boundary

The Apache-2.0 license for DocuAgent Community does not relicense third-party packages. Users obtain those packages from their registries under the licenses declared by each package.

This review is sufficient for the source-only repository described above. Any future release that ships a compiled frontend, Electron application, installer, vendored package, font, or other bundled third-party content requires a notice inventory generated from that exact artifact before publication.
