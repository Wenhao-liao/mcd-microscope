#!/usr/bin/env node
/**
 * 把 assets/*.html 渲染成 2x 视网膜截图（PNG），并自动裁掉底部多余留白。
 *
 * 为什么需要它：GitHub README 里没人会去点开 .html 文件，必须给出 PNG 才能被看到。
 *
 * 依赖（二选一）：
 *   - jimp 已装在 NODE_PATH 指向的目录，例如：
 *       NODE_PATH=/path/to/node_modules node scripts/render_cards.cjs
 *   - 或直接 `npm i jimp` 后 `node scripts/render_cards.cjs`
 *   另外需要本机装有 Google Chrome。
 *
 * 用法：
 *   node scripts/render_cards.cjs           # 渲染全部卡片
 *   node scripts/render_cards.cjs demo      # 只渲染名字含 demo 的卡片
 */

const { execFileSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { dirname, resolve, basename } = require("node:path");

const ROOT = resolve(__dirname, "..");
const CHROME =
  process.env.CHROME_PATH ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const SCALE = 2; // @2x，视网膜
const WIDTH = 680; // CSS 像素宽
const MAX_HEIGHT = 2600; // 视口高度上限，够装下整张卡
const BOTTOM_PAD = 72; // 裁完后保留的底部留白（CSS 像素）

const CARDS = [
  { src: "assets/nutrition-map.html", out: "assets/nutrition-map.png" },
  { src: "assets/demo.html", out: "assets/demo.png" },
];

function loadJimp() {
  const candidates = [
    "jimp",
    ...(process.env.NODE_PATH || "")
      .split(":")
      .filter(Boolean)
      .map((p) => resolve(p, "jimp")),
  ];
  for (const c of candidates) {
    try {
      return require(c);
    } catch (_) {
      /* 继续试下一个 */
    }
  }
  console.error(
    "找不到 jimp。请任选一种：\n" +
      "  1) npm i jimp\n" +
      "  2) NODE_PATH=<你的 node_modules 目录> node scripts/render_cards.cjs"
  );
  process.exit(1);
}

function shoot(htmlPath, outPath) {
  execFileSync(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--no-sandbox",
      "--no-first-run",
      `--force-device-scale-factor=${SCALE}`,
      `--window-size=${WIDTH},${MAX_HEIGHT}`,
      "--virtual-time-budget=3000",
      `--screenshot=${outPath}`,
      `file://${htmlPath}`,
    ],
    { stdio: ["ignore", "ignore", "pipe"] }
  );
}

/** 从底部往上找第一条「不是纯背景色」的像素行。 */
function lastContentRow(image, bg) {
  const { width, height } = image.bitmap;
  for (let y = height - 1; y >= 0; y--) {
    for (let x = 0; x < width; x += 3) {
      if (image.getPixelColor(x, y) !== bg) return y;
    }
  }
  return height - 1;
}

async function main() {
  if (!existsSync(CHROME)) {
    console.error(`找不到 Chrome：${CHROME}\n可用 CHROME_PATH 环境变量指定。`);
    process.exit(1);
  }
  const Jimp = loadJimp();
  const filter = process.argv[2];

  for (const { src, out } of CARDS) {
    if (filter && !basename(src).includes(filter)) continue;

    const htmlPath = resolve(ROOT, src);
    const outPath = resolve(ROOT, out);
    const rawPath = outPath.replace(/\.png$/, ".raw.png");

    shoot(htmlPath, rawPath);
    const img = await Jimp.read(rawPath);

    // 背景色取左上角像素（HTML 背景铺满整页）
    const bg = img.getPixelColor(0, 0);
    const cut = Math.min(
      img.bitmap.height,
      lastContentRow(img, bg) + BOTTOM_PAD * SCALE
    );
    img.crop(0, 0, img.bitmap.width, cut);
    await img.writeAsync(outPath);

    // 清掉中间产物
    try {
      require("node:fs").unlinkSync(rawPath);
    } catch (_) {}

    console.log(`${src} → ${out}  ${img.bitmap.width}x${img.bitmap.height}`);
  }
}

main();
