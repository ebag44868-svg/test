// 파이썬 실행 워커 (Pyodide = 브라우저용 CPython). 모듈 워커로 실행됨.
import { loadPyodide } from "./pyodide.mjs";

let run = null;

async function boot() {
  try {
    const py = await loadPyodide({
      indexURL: new URL("./", import.meta.url).href,
      stdout: () => {},
      stderr: () => {},
    });
    const src = await (await fetch(new URL("./runner.py", import.meta.url))).text();
    py.runPython(src);
    run = py.globals.get("run_user");
    const ver = py.runPython("import sys; sys.version.split()[0]");
    postMessage({ type: "ready", version: ver });
  } catch (e) {
    postMessage({ type: "bootError", message: String(e && e.message || e) });
  }
}
const booting = boot();

onmessage = async (ev) => {
  const { id, code, stdin, record, softLimit } = ev.data;
  await booting;
  if (!run) {
    postMessage({ type: "result", id, res: { stdout: "", error: { type: "EngineError", msg: "파이썬 엔진을 불러오지 못함", tb: "" } } });
    return;
  }
  let res;
  try {
    res = JSON.parse(run(code, stdin || "", !!record, softLimit || 6));
  } catch (e) {
    res = { stdout: "", error: { type: "EngineError", msg: String(e && e.message || e), tb: "" } };
  }
  postMessage({ type: "result", id, res });
};
