#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::ipc::{InvokeBody, Request};
use tauri::{Emitter, WebviewUrl, WebviewWindowBuilder};

fn project_root() -> Result<PathBuf, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    executable
        .ancestors()
        .find(|folder| folder.join("work/plane/desktop/plane_bridge.py").is_file())
        .map(Path::to_path_buf)
        .ok_or_else(|| "未找到古代战争项目，请从项目的 outputs 目录启动。".into())
}

fn run_bridge(root: &Path, request: Value, progress: impl Fn(Value)) -> Result<Value, String> {
    let temp = root.join("work/tmp");
    std::fs::create_dir_all(&temp).map_err(|error| error.to_string())?;
    let bundled_python = root.join("runtime/python/python.exe");
    let python = if bundled_python.is_file() {
        bundled_python
    } else {
        std::env::var_os("PLANE_ARENA_PYTHON").map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("python.exe"))
    };
    let mut command = Command::new(python);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .args(["-B", "-X", "utf8", "-m", "desktop.plane_bridge"])
        .current_dir(root.join("work/plane"))
        .env("TEMP", &temp)
        .env("TMP", &temp)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("无法启动项目的 Python：{error}"))?;

    let mut stderr = child.stderr.take().unwrap();
    let errors = std::thread::spawn(move || {
        let mut text = String::new();
        let _ = stderr.read_to_string(&mut text);
        text
    });
    let input_error = child.stdin.take().unwrap().write_all(request.to_string().as_bytes()).err();
    let mut result = None;
    for line in BufReader::new(child.stdout.take().unwrap()).lines() {
        let Ok(line) = line else { break };
        if let Ok(mut message) = serde_json::from_str::<Value>(&line) {
            match message["kind"].as_str() {
                Some("progress") => progress(message),
                Some("result") => result = Some(Ok(message["value"].take())),
                Some("error") => {
                    result = Some(Err(message["message"].as_str().unwrap_or("计算未完成").to_owned()));
                }
                _ => {}
            }
        }
    }
    let status = child.wait().map_err(|error| error.to_string())?;
    let detail = errors.join().unwrap_or_default();
    match result {
        Some(Err(error)) => Err(error),
        Some(Ok(value)) if status.success() => Ok(value),
        _ => {
            let explanation = detail.lines().last().map(str::to_owned)
                .or_else(|| input_error.map(|error| error.to_string())).unwrap_or_else(|| status.to_string());
            Err(format!("模拟器中断：{explanation}"))
        }
    }
}

#[tauri::command]
async fn plane_request(app: tauri::AppHandle, request: Value) -> Result<Value, String> {
    let root = project_root()?;
    tauri::async_runtime::spawn_blocking(move || {
        run_bridge(&root, request, |message| { let _ = app.emit("plane-progress", message); })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn save_result_image(request: Request<'_>) -> Result<String, String> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err("没有收到 PNG 图片。".into());
    };
    if bytes.len() > 16 * 1024 * 1024 || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err("结果图不是有效的 PNG，或超过 16 MB。".into());
    }
    let directory = project_root()?.join("outputs/位面对战结果图");
    let error_message = |error| format!("无法保存结果图：{error}");
    std::fs::create_dir_all(&directory).map_err(error_message)?;
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?.as_millis();
    let path = directory.join(format!("位面战-{stamp}.png"));
    let mut file = std::fs::OpenOptions::new().write(true).create_new(true)
        .open(&path).map_err(error_message)?;
    if let Err(error) = file.write_all(bytes) {
        drop(file);
        let _ = std::fs::remove_file(&path);
        return Err(error_message(error));
    }
    Ok(path.to_string_lossy().into_owned())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![plane_request, save_result_image])
        .setup(|app| {
            let root = project_root().map_err(std::io::Error::other)?;
            let data = root.join("work/tmp/plane-arena-webview");
            std::fs::create_dir_all(&data)?;
            let hidden = std::env::var_os("PLANE_ARENA_VERIFY_HIDDEN").is_some();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("古代战争 | 位面对战台")
                .inner_size(1160.0, 665.0)
                .min_inner_size(740.0, 570.0)
                .data_directory(data)
                .visible(!hidden)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("arena application error");
}
