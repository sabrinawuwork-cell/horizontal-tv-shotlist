#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const BASE_HEADERS = ["镜号", "片段", "时长", "分镜内容", "Seedance提示词"];
const NOTE_HEADERS = [...BASE_HEADERS, "备注"];

function argsOf(argv) {
  const args = { previewDir: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--input") args.input = argv[++i];
    else if (argv[i] === "--output") args.output = argv[++i];
    else if (argv[i] === "--preview-dir") args.previewDir = argv[++i];
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (!args.input || !args.output) throw new Error("Usage: build_stage3_xlsx.mjs --input stage3.md --output stage3.xlsx");
  return args;
}

function frontmatter(text) {
  const lines = text.split(/\r?\n/);
  const end = lines.findIndex((line, i) => i > 0 && line.trim() === "---");
  const out = {};
  for (const line of lines.slice(1, end)) {
    const split = line.indexOf(":");
    if (split < 0 || /^\s/.test(line)) continue;
    out[line.slice(0, split).trim()] = line.slice(split + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  return out;
}

function splitRow(line) {
  const value = line.trim();
  if (!value.startsWith("|")) return null;
  const cells = [];
  let current = "";
  let escaped = false;
  for (const char of value.slice(1)) {
    if (escaped) { current += char; escaped = false; }
    else if (char === "\\") { current += char; escaped = true; }
    else if (char === "|") { cells.push(current.trim()); current = ""; }
    else current += char;
  }
  if (current || !value.endsWith("|")) cells.push(current.trim());
  return cells;
}

function parse(text) {
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length - 1; i += 1) {
    const header = splitRow(lines[i]);
    const separator = splitRow(lines[i + 1]);
    const validHeader = header && [BASE_HEADERS, NOTE_HEADERS].some((candidate) => header.join("|") === candidate.join("|"));
    if (!validHeader || !separator?.every((cell) => /^:?-{3,}:?$/.test(cell))) continue;
    const rows = [];
    for (let j = i + 2; j < lines.length; j += 1) {
      const row = splitRow(lines[j]);
      if (!row) break;
      if (row.length !== header.length) throw new Error(`Stage 3 row ${j + 1} does not match its header count`);
      const duration = Number(row[2].replace(/\s*(?:s|秒)$/i, ""));
      if (!Number.isFinite(duration)) throw new Error(`Invalid duration ${row[2]}`);
      const cleanBreaks = (value) => value
        .replace(/(?:<br\s*\/?>){2}/gi, "\n\n")
        .replace(/<br\s*\/?>/gi, "\n");
      const values = [row[0], row[1], duration, cleanBreaks(row[3]), cleanBreaks(row[4])];
      if (header.length === 6) values.push(cleanBreaks(row[5]));
      rows.push(values);
    }
    if (header.length === 6 && !rows.some((row) => String(row[5] || "").trim())) {
      throw new Error("Stage 3 cannot keep an empty remarks column when no issue exists");
    }
    return { headers: header, rows };
  }
  throw new Error("No exact Stage 3 five-column table found; a sixth remarks column is allowed only when needed");
}

async function main() {
  const args = argsOf(process.argv.slice(2));
  const text = await fs.readFile(args.input, "utf8");
  const meta = frontmatter(text);
  const parsed = parse(text);
  const { headers, rows } = parsed;
  const hasNotes = headers.length === 6;
  const endColumn = hasNotes ? "F" : "E";
  const workbook = Workbook.create();
  const info = workbook.worksheets.add("提示词说明");
  const sheet = workbook.worksheets.add("Seedance提示词");
  info.showGridLines = false;
  sheet.showGridLines = false;

  info.getRange(`A1:${endColumn}1`).merge();
  info.getRange("A1").values = [[`${meta.project || "项目"} ${meta.episode || ""}｜Stage 3 Seedance提示词`]];
  info.getRange(`A1:${endColumn}1`).format = { fill: "#162B3A", font: { name: "Aptos Display", size: 18, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
  info.getRange(`A1:${endColumn}1`).format.rowHeight = 36;
  const uniqueSegments = [...new Set(rows.map((row) => row[1]))];
  info.getRange("A3:B9").values = [
    ["项目", meta.project || ""], ["集号", meta.episode || ""], ["版本 / 状态", `${meta.version || ""} / ${meta.status || ""}`],
    ["模型", meta.model || "seedance-2.5"], ["镜头数", rows.length], ["片段数", uniqueSegments.length], ["总时长", null],
  ];
  info.getRange("A3:A9").format = { fill: "#DCE8EE", font: { bold: true, color: "#203744" } };
  info.getRange("B9").formulas = [[`=SUM('Seedance提示词'!C4:C${rows.length + 3})`]];
  info.getRange("B9").format.numberFormat = "0.0\"s\"";
  info.getRange(`A11:${endColumn}11`).merge();
  info.getRange("A11").values = [["规则说明"]];
  info.getRange(`A11:${endColumn}11`).format = { fill: "#276678", font: { bold: true, color: "#FFFFFF" } };
  info.getRange(`A12:${endColumn}15`).merge(true);
  info.getRange(`A12:${endColumn}15`).values = [
    ["片段按强制边界、单镜不可拆、30秒硬上限、主体稳定性、动作完整性、27–30秒目标依次决策。"],
    ["每个片段从0秒累计；分镜内容和提示词的类别之间均保留一个空行。"],
    ["draft显示固定类别名称；approved只能由Stage 2批准文本机械展开。"],
    ["有问题才显示备注列；逐行使用🔴阻塞、🟠风险、🟡待确认、🔵提醒；Markdown是权威底稿。"],
  ];
  info.getRange(`A12:${endColumn}15`).format = { fill: "#F2F7F9", wrapText: true, verticalAlignment: "center" };
  info.getRange("A:A").format.columnWidth = 18;
  info.getRange("B:B").format.columnWidth = 42;
  info.getRange(`C:${endColumn}`).format.columnWidth = 14;
  info.getRange("A3:B9").format.rowHeight = 25;
  info.getRange(`A12:${endColumn}15`).format.rowHeight = 28;

  sheet.getRange(`A1:${endColumn}1`).merge();
  sheet.getRange("A1").values = [[`${meta.project || "项目"}｜Stage 3 ${meta.status || "draft"}`]];
  sheet.getRange(`A1:${endColumn}1`).format = { fill: "#162B3A", font: { name: "Aptos Display", size: 16, bold: true, color: "#FFFFFF" } };
  sheet.getRange(`A3:${endColumn}3`).values = [headers];
  sheet.getRange(`A3:${endColumn}3`).format = { fill: "#276678", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center" };
  const lastRow = rows.length + 3;
  sheet.getRange(`A4:${endColumn}${lastRow}`).values = rows;
  sheet.getRange(`A4:${endColumn}${lastRow}`).format = { font: { name: "Aptos", size: 10, color: "#1F2933" }, wrapText: true, verticalAlignment: "top", borders: { insideHorizontal: { style: "thin", color: "#D7E1E7" } } };
  sheet.getRange(`A4:C${lastRow}`).format.horizontalAlignment = "center";
  sheet.getRange(`C4:C${lastRow}`).format.numberFormat = "0.0\"s\"";
  sheet.getRange(`A4:${endColumn}${lastRow}`).format.rowHeight = 250;
  sheet.getRange("A:A").format.columnWidth = 12;
  sheet.getRange("B:B").format.columnWidth = 12;
  sheet.getRange("C:C").format.columnWidth = 10;
  sheet.getRange("D:D").format.columnWidth = 70;
  sheet.getRange("E:E").format.columnWidth = 100;
  if (hasNotes) sheet.getRange("F:F").format.columnWidth = 32;
  sheet.freezePanes.freezeRows(3);
  sheet.freezePanes.freezeColumns(2);
  const table = sheet.tables.add(`A3:${endColumn}${lastRow}`, true, "SeedancePrompts");
  table.style = "TableStyleMedium2";

  const outputPath = path.resolve(args.output);
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await (await SpreadsheetFile.exportXlsx(workbook)).save(outputPath);
  const persisted = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const values = persisted.worksheets.getItem("Seedance提示词").getRange(`A4:${endColumn}${lastRow}`).values;
  if (values.length !== rows.length || values.some((row, i) => String(row[0]) !== String(rows[i][0]))) throw new Error("Persisted workbook differs from Markdown");
  const inspection = await persisted.inspect({ kind: "table,formula", maxChars: 2500, tableMaxRows: 4, tableMaxCols: headers.length });
  process.stdout.write(`${inspection.ndjson}\n`);
  const formulaErrors = await persisted.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
  process.stdout.write(`${formulaErrors.ndjson}\n`);
  if (args.previewDir) {
    const previewDir = path.resolve(args.previewDir);
    await fs.mkdir(previewDir, { recursive: true });
    for (const [name, sheetName] of [["Stage3说明.png", "提示词说明"], ["Stage3提示词.png", "Seedance提示词"]]) {
      const image = await persisted.render({ sheetName, autoCrop: "all", scale: 1.1, format: "png" });
      await fs.writeFile(path.join(previewDir, name), new Uint8Array(await image.arrayBuffer()));
    }
  }
  process.stdout.write(`${JSON.stringify({ output: outputPath, rows: rows.length, segments: uniqueSegments.length })}\n`);
}

main().catch((error) => { process.stderr.write(`${error.stack || error.message}\n`); process.exit(1); });
