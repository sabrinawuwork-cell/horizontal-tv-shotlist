#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const HEADERS = ["场次", "镜号", "时长", "摄影机", "画面内容", "音效", "剪辑衔接"];
const scriptDir = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const args = { previewDir: null, validator: path.join(scriptDir, "validate_shotlist.py") };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--input") args.input = argv[++index];
    else if (token === "--output") args.output = argv[++index];
    else if (token === "--preview-dir") args.previewDir = argv[++index];
    else if (token === "--validator") args.validator = argv[++index];
    else if (token === "--python") args.python = argv[++index];
    else if (token === "--source-root") args.sourceRoot = argv[++index];
    else throw new Error(`Unknown argument: ${token}`);
  }
  if (!args.input || !args.output) {
    throw new Error("Usage: build_xlsx.mjs --input shotlist.md --output shotlist.xlsx [--preview-dir DIR]");
  }
  return args;
}

function parseFrontmatter(text) {
  const lines = text.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") throw new Error("Missing frontmatter");
  const closing = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (closing < 0) throw new Error("Unclosed frontmatter");
  const values = {};
  for (const line of lines.slice(1, closing)) {
    if (!line.trim() || /^\s/.test(line) || !line.includes(":")) continue;
    const split = line.indexOf(":");
    const key = line.slice(0, split).trim();
    let value = line.slice(split + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function splitRow(line) {
  const stripped = line.trim();
  if (!stripped.startsWith("|")) return null;
  const cells = [];
  let current = "";
  let escaped = false;
  let inCode = false;
  for (const char of stripped.slice(1)) {
    if (escaped) {
      current += char;
      escaped = false;
    } else if (char === "\\") {
      current += char;
      escaped = true;
    } else if (char === "`") {
      current += char;
      inCode = !inCode;
    } else if (char === "|" && !inCode) {
      cells.push(current.trim());
      current = "";
    } else current += char;
  }
  if (current || !stripped.endsWith("|")) cells.push(current.trim());
  return cells;
}

function isSeparator(cells) {
  return cells?.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replaceAll(" ", "")));
}

function extractSceneNotes(lines, start, end) {
  const labels = new Set(["场景图", "戏剧与节奏", "视点与调度", "空间与轴线", "基础全景机位与主轴线", "光影基准", "far-side key基准", "场次时长"]);
  const notes = [];
  for (let index = start; index < end; index += 1) {
    const stripped = lines[index].trim();
    const inline = stripped.match(/^(?:[-*]\s+)?(场景图|戏剧与节奏|视点与调度|空间与轴线|基础全景机位与主轴线|光影基准|far-side key基准|场次时长)\s*[:：]\s*(.+)$/i);
    if (inline) {
      notes.push(`${inline[1]}：${inline[2]}`);
      continue;
    }
    const heading = stripped.match(/^#{3,5}\s*(场景图|戏剧与节奏|视点与调度|空间与轴线|基础全景机位与主轴线|光影基准|far-side key基准|场次时长)(?:\s*[:：]\s*(.*))?$/i);
    if (!heading || !labels.has(heading[1])) continue;
    const content = [];
    if (heading[2]) content.push(heading[2].trim());
    for (let cursor = index + 1; cursor < end; cursor += 1) {
      const candidate = lines[cursor].trim();
      if (candidate.startsWith("#")) break;
      if (candidate) content.push(candidate.replace(/^[-*]\s+/, ""));
    }
    if (content.length) notes.push(`${heading[1]}：${content.join(" ")}`);
  }
  return [...new Set(notes)];
}

function parseTables(text) {
  const lines = text.split(/\r?\n/);
  const tables = [];
  let index = 0;
  while (index + 1 < lines.length) {
    const headers = splitRow(lines[index]);
    const separator = splitRow(lines[index + 1]);
    if (!headers || !isSeparator(separator)) {
      index += 1;
      continue;
    }
    const rows = [];
    let rowIndex = index + 2;
    while (rowIndex < lines.length) {
      const row = splitRow(lines[rowIndex]);
      if (!row || isSeparator(row)) break;
      rows.push(row);
      rowIndex += 1;
    }
    let context = "";
    let sceneHeading = "";
    let sceneStart = 0;
    for (let prior = index - 1; prior >= 0; prior -= 1) {
      const heading = lines[prior].trim().match(/^(#{2,4})\s+(.+)$/);
      if (!heading) continue;
      if (!context) context = heading[2].trim();
      if (/^场次\s+/.test(heading[2].trim())) {
        sceneHeading = heading[2].trim();
        sceneStart = prior;
        break;
      }
    }
    const notes = extractSceneNotes(lines, sceneStart + 1, index);
    tables.push({ headers, rows, context, sceneHeading, notes });
    index = Math.max(index + 2, rowIndex);
  }
  return tables.filter((table) => table.headers.some((header) => HEADERS.includes(header)));
}

function parseSeconds(value) {
  const match = String(value).trim().match(/^(\d+(?:\.\d+)?)\s*(?:s|秒)$/i);
  if (!match) throw new Error(`Invalid duration: ${value}`);
  return Number(match[1]);
}

function columnLetter(column) {
  let value = column;
  let output = "";
  while (value > 0) {
    value -= 1;
    output = String.fromCharCode(65 + (value % 26)) + output;
    value = Math.floor(value / 26);
  }
  return output;
}

function safeName(value) {
  return value.replace(/[^A-Za-z0-9]/g, "").slice(0, 18) || "Scene";
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const python = args.python || process.env.HORIZONTAL_SHOTLIST_PYTHON || "python3";
  const validationArgs = [args.validator, args.input];
  if (args.sourceRoot) validationArgs.push("--source-root", args.sourceRoot);
  const validation = spawnSync(python, validationArgs, { encoding: "utf8" });
  if (validation.status !== 0) {
    throw new Error(`Markdown validation failed:\n${validation.stdout}${validation.stderr}`);
  }

  const text = await fs.readFile(args.input, "utf8");
  const metadata = parseFrontmatter(text);
  const tables = parseTables(text);
  if (!tables.length) throw new Error("No shotlist tables found");

  const workbook = Workbook.create();
  const cover = workbook.worksheets.add("分镜说明");
  const sheet = workbook.worksheets.add("全集分镜");
  cover.showGridLines = false;
  sheet.showGridLines = false;

  sheet.getRange("A1:G1").merge();
  sheet.getRange("A1").values = [[`${metadata.project || "项目"} ${metadata.episode || ""}｜横屏剧集分镜`]];
  sheet.getRange("A1:G1").format = {
    fill: "#1C2434",
    font: { name: "Aptos Display", size: 18, bold: true, color: "#FFFFFF" },
    verticalAlignment: "center",
  };
  sheet.getRange("A1:G1").format.rowHeight = 34;

  let outputRow = 3;
  let lastScene = "";
  let tableIndex = 0;
  const mainDurationRanges = [];
  const mainIdRanges = [];
  const coverageIdRanges = [];

  for (const table of tables) {
    if (table.headers.join("|") !== HEADERS.join("|")) {
      throw new Error(`Unexpected headers: ${table.headers.join(" | ")}`);
    }
    const coverage = table.context.includes("必要覆盖") || table.rows.every((row) => row[1]?.toUpperCase().includes("-COV"));
    const scene = table.rows[0]?.[0] || table.sceneHeading.replace(/^场次\s+/, "") || "未命名";
    if (scene !== lastScene) {
      if (lastScene) outputRow += 1;
      sheet.getRange(`A${outputRow}:G${outputRow}`).merge();
      sheet.getRange(`A${outputRow}`).values = [[table.sceneHeading || `场次 ${scene}`]];
      sheet.getRange(`A${outputRow}:G${outputRow}`).format = {
        fill: "#334E68",
        font: { bold: true, color: "#FFFFFF", size: 12 },
        verticalAlignment: "center",
      };
      sheet.getRange(`A${outputRow}:G${outputRow}`).format.rowHeight = 26;
      outputRow += 1;
      const notes = table.notes.length ? table.notes : ["场景导演说明见权威 Markdown。"];
      for (const note of notes) {
        sheet.getRange(`A${outputRow}:G${outputRow}`).merge();
        sheet.getRange(`A${outputRow}`).values = [[note]];
        sheet.getRange(`A${outputRow}:G${outputRow}`).format = {
          fill: "#EDF2F7",
          font: { color: "#243B53", size: 10 },
          wrapText: true,
          verticalAlignment: "center",
        };
        sheet.getRange(`A${outputRow}:G${outputRow}`).format.rowHeight = 24;
        outputRow += 1;
      }
      lastScene = scene;
    }

    if (coverage) {
      sheet.getRange(`A${outputRow}:G${outputRow}`).merge();
      sheet.getRange(`A${outputRow}`).values = [["必要覆盖｜不计入成片时长"]];
      sheet.getRange(`A${outputRow}:G${outputRow}`).format = {
        fill: "#FFF1D6",
        font: { bold: true, color: "#8A4B08" },
      };
      outputRow += 1;
    }

    const headerRow = outputRow;
    sheet.getRange(`A${headerRow}:G${headerRow}`).values = [HEADERS];
    sheet.getRange(`A${headerRow}:G${headerRow}`).format = {
      fill: coverage ? "#B7791F" : "#1F5D6B",
      font: { bold: true, color: "#FFFFFF", size: 10 },
      horizontalAlignment: "center",
      verticalAlignment: "center",
      wrapText: true,
    };
    sheet.getRange(`A${headerRow}:G${headerRow}`).format.rowHeight = 28;
    const firstDataRow = headerRow + 1;
    const data = table.rows.map((row) => row.map((cell, index) => (index === 2 ? parseSeconds(cell) : cell)));
    const lastDataRow = firstDataRow + data.length - 1;
    if (data.length) {
      sheet.getRange(`A${firstDataRow}:G${lastDataRow}`).values = data;
      sheet.getRange(`A${firstDataRow}:G${lastDataRow}`).format = {
        fill: coverage ? "#FFF8E8" : "#FFFFFF",
        font: { name: "Aptos", size: 10, color: "#1F2933" },
        verticalAlignment: "center",
        wrapText: true,
        borders: { insideHorizontal: { style: "thin", color: "#D9E2EC" } },
      };
      sheet.getRange(`A${firstDataRow}:C${lastDataRow}`).format.horizontalAlignment = "center";
      sheet.getRange(`C${firstDataRow}:C${lastDataRow}`).format.numberFormat = "0.0\"s\"";
      sheet.getRange(`A${firstDataRow}:G${lastDataRow}`).format.rowHeight = 68;
      const tableName = `${coverage ? "Coverage" : "Shots"}${safeName(scene)}${tableIndex}`;
      sheet.tables.add(`A${headerRow}:G${lastDataRow}`, true, tableName).style = coverage ? "TableStyleMedium9" : "TableStyleMedium2";
      if (coverage) coverageIdRanges.push(`B${firstDataRow}:B${lastDataRow}`);
      else {
        mainDurationRanges.push(`C${firstDataRow}:C${lastDataRow}`);
        mainIdRanges.push(`B${firstDataRow}:B${lastDataRow}`);
      }
    }
    outputRow = lastDataRow + 2;
    tableIndex += 1;
  }

  sheet.getRange("A:A").format.columnWidth = 8;
  sheet.getRange("B:B").format.columnWidth = 12;
  sheet.getRange("C:C").format.columnWidth = 10;
  sheet.getRange("D:D").format.columnWidth = 44;
  sheet.getRange("E:E").format.columnWidth = 78;
  sheet.getRange("F:F").format.columnWidth = 28;
  sheet.getRange("G:G").format.columnWidth = 38;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);

  cover.getRange("A1:F1").merge();
  cover.getRange("A1").values = [[`${metadata.project || "项目"}｜横屏剧集分镜说明`]];
  cover.getRange("A1:F1").format = {
    fill: "#1C2434",
    font: { name: "Aptos Display", size: 20, bold: true, color: "#FFFFFF" },
  };
  cover.getRange("A1:F1").format.rowHeight = 38;
  const summaryRows = [
    ["项目", metadata.project || ""],
    ["集号", metadata.episode || ""],
    ["版本 / 状态", `${metadata.version || ""} / ${metadata.status || ""}`],
    ["画幅", metadata.aspect_ratio || "16:9"],
    ["拍摄语言", metadata.shooting_language || ""],
    ["制作配置", metadata.production_profile || "ai-video"],
    ["成片主镜数", null],
    ["必要覆盖数", null],
    ["成片总时长", null],
  ];
  cover.getRange("A3:B11").values = summaryRows;
  cover.getRange("A3:A11").format = {
    fill: "#D9E2EC",
    font: { bold: true, color: "#243B53" },
    verticalAlignment: "center",
  };
  cover.getRange("B3:B11").format = { wrapText: true, verticalAlignment: "center" };
  cover.getRange("A3:B11").format.borders = { insideHorizontal: { style: "thin", color: "#BCCCDC" } };
  const quotedMainIds = mainIdRanges.map((range) => `'全集分镜'!${range}`);
  const quotedCoverageIds = coverageIdRanges.map((range) => `'全集分镜'!${range}`);
  const quotedDurations = mainDurationRanges.map((range) => `'全集分镜'!${range}`);
  cover.getRange("B9").formulas = [[quotedMainIds.length ? `=COUNTA(${quotedMainIds.join(",")})` : "=0"]];
  cover.getRange("B10").formulas = [[quotedCoverageIds.length ? `=COUNTA(${quotedCoverageIds.join(",")})` : "=0"]];
  cover.getRange("B11").formulas = [[quotedDurations.length ? `=SUM(${quotedDurations.join(",")})` : "=0"]];
  cover.getRange("B9:B10").format.numberFormat = "0";
  cover.getRange("B11").format.numberFormat = "0.0\"s\"";
  cover.getRange("A13:F13").merge();
  cover.getRange("A13").values = [["使用说明"]];
  cover.getRange("A13:F13").format = { fill: "#1F5D6B", font: { bold: true, color: "#FFFFFF" } };
  cover.getRange("A14:F17").merge(true);
  cover.getRange("A14:F17").values = [
    ["Markdown 是唯一权威底稿；本工作簿只能由底稿重新生成。"],
    ["成片总时长与主镜数不统计必要覆盖。"],
    ["对白位于画面内容；音效列只写动作声、环境声和剧情画内声源。"],
    ["每场必须以场景图建立空间、基础机位、主轴线和far-side key基准；无法可靠判断的反打背景不得虚构。"],
  ];
  cover.getRange("A14:F17").format = { fill: "#F0F4F8", wrapText: true, verticalAlignment: "center" };
  cover.getRange("A:A").format.columnWidth = 18;
  cover.getRange("B:B").format.columnWidth = 46;
  cover.getRange("C:F").format.columnWidth = 12;
  cover.getRange("A3:B11").format.rowHeight = 26;
  cover.getRange("A14:F17").format.rowHeight = 30;

  const outputPath = path.resolve(args.output);
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(outputPath);

  const persisted = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const persistedRows = persisted.worksheets.getItem("全集分镜").getUsedRange().values;
  const expectedRows = tables.flatMap((table) =>
    table.rows.map((row) => row.map((cell, index) => (index === 2 ? parseSeconds(cell) : cell))),
  );
  const expectedIds = new Set(expectedRows.map((row) => String(row[1])));
  const actualRows = persistedRows.filter((row) => expectedIds.has(String(row[1])));
  if (actualRows.length !== expectedRows.length) {
    throw new Error(`Persisted workbook row count ${actualRows.length} does not match Markdown ${expectedRows.length}`);
  }
  for (let index = 0; index < expectedRows.length; index += 1) {
    const expected = expectedRows[index].map((value) => value ?? "");
    const actual = actualRows[index].map((value) => value ?? "");
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error(`Persisted workbook differs from Markdown at shot ${expected[1]}`);
    }
  }

  const inspection = await persisted.inspect({
    kind: "sheet,table,formula",
    maxChars: 5000,
    tableMaxRows: 4,
    tableMaxCols: 9,
  });
  process.stdout.write(`${inspection.ndjson}\n`);
  const errors = await persisted.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
  });
  process.stdout.write(`${errors.ndjson}\n`);

  if (args.previewDir) {
    const previewDir = path.resolve(args.previewDir);
    await fs.mkdir(previewDir, { recursive: true });
    for (const [name, sheetName] of [["说明页.png", "分镜说明"], ["全集分镜.png", "全集分镜"]]) {
      const image = await persisted.render({ sheetName, autoCrop: "all", scale: 1.2, format: "png" });
      await fs.writeFile(path.join(previewDir, name), new Uint8Array(await image.arrayBuffer()));
    }
  }
  process.stdout.write(`${JSON.stringify({ output: outputPath, tables: tables.length, rows: outputRow - 1 })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
