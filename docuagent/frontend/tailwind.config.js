/** Terminal/hacker tokens: phosphor green on near-black, monospace throughout.
 *  Kept as semantic tokens so the information architecture does not need to change
 *  when the visual skin does. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: { DEFAULT: "#030604", light: "#050A07", raise: "#071009", float: "#0B160E", high: "#102217" },
        ink: { DEFAULT: "#6CFFA8", dim: "#2FBF70", ghost: "#123A24" },
        vermilion: { DEFAULT: "#FF5C63", deep: "#C62F45" },
        chalk: { DEFAULT: "#E9FFF1", dim: "#9FE3BB", faint: "#65A67F" },
      },
      fontFamily: {
        display: ['"Cascadia Code"', '"SFMono-Regular"', "Consolas", '"Microsoft YaHei"', "monospace"],
        body: ['"Cascadia Code"', '"SFMono-Regular"', "Consolas", '"Microsoft YaHei"', "monospace"],
        mono: ['"Cascadia Code"', '"SFMono-Regular"', "Consolas", "ui-monospace", '"Microsoft YaHei"', "monospace"],
        hand: ['"Cascadia Code"', '"SFMono-Regular"', "Consolas", '"Microsoft YaHei"', "monospace"],
      },
      transitionTimingFunction: {
        ui: "cubic-bezier(0.2, 0.8, 0.2, 1)",
      },
    },
  },
  plugins: [],
};
