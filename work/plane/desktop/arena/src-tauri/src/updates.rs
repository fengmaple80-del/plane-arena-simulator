use serde_json::Value;
use std::{fs, path::{Path,PathBuf},process::Command};
use tauri::AppHandle;

const EXE: &str = "位面对战台.exe";
fn valid_folder(name: &str) -> bool {
    name.starts_with('v') && name.len()<80 && name.contains('-') && name.bytes().all(|c|c.is_ascii_alphanumeric() || matches!(c,b'.'|b'-'|b'_'))
}
pub fn base(root: &Path) -> &Path {
    if root.file_name().and_then(|s|s.to_str()).is_some_and(valid_folder) {
        if let Some(parent)=root.parent().filter(|p| p.file_name().is_some_and(|s| s=="updates")) {
            if let Some(base)=parent.parent().filter(|p|p.join(EXE).is_file()) {return base;}
        }
    }
    root
}
pub fn redirect(root:&Path)->bool {
    if base(root)!=root {return false;}
    let Ok(name)=fs::read_to_string(root.join("updates/current.txt")) else {return false;};
    if !valid_folder(name.trim()) {return false;}
    let dest=root.join("updates").join(name.trim());
    Command::new(dest.join(EXE)).current_dir(dest).spawn().is_ok()
}
pub fn activate(root:&Path)->std::io::Result<()> {
    let install=base(root);
    if install!=root && std::env::args().any(|s|s=="--activate-update") {
        let updates=install.join("updates");
        let temp=updates.join(format!("current-{}.tmp",std::process::id()));
        fs::write(&temp,root.file_name().unwrap().to_string_lossy().as_bytes())?;
        fs::rename(temp,updates.join("current.txt"))?;
    }
    Ok(())
}
fn run(install:bool)->Result<Value,String>{
    let root=super::project_root()?;
    let base=base(&root);
    let mut cmd=Command::new(root.join("runtime/python/python.exe"));
    #[cfg(windows)] {use std::os::windows::process::CommandExt; cmd.creation_flags(0x08000000);}
    let out=cmd.args(["-B","-X","utf8"]).arg(root.join("updater/update.py")).arg(if install {"install"}else{"check"}).arg(env!("CARGO_PKG_VERSION")).arg(base).output().map_err(|e|e.to_string())?;
    let reply:Value=serde_json::from_slice(&out.stdout).map_err(|_|"无法读取更新结果，请检查便携包是否完整。".to_string())?;
    if !out.status.success(){return Err(reply["error"].as_str().unwrap_or("检查更新失败").to_string());}
    if let Some(folder)=reply["folder"].as_str(){
        if !install || !valid_folder(folder){return Err("更新目录异常".into());}
        let dest=base.join("updates").join(folder);
        for rel in ["data/local-catalog.json","data/monizhan-local.json"] {
            let file=root.join(rel);
            if file.is_file(){fs::copy(&file,dest.join(rel)).map_err(|e|e.to_string())?;}
        }
        Command::new(dest.join(EXE)).arg("--activate-update").current_dir(&dest).spawn().map_err(|e|format!("无法打开新版：{e}"))?;
    }
    Ok(reply)
}
#[tauri::command]
pub async fn portable_update(app:AppHandle,install:bool)->Result<Value,String>{
    let reply=tauri::async_runtime::spawn_blocking(move||run(install)).await.map_err(|e|e.to_string())??;
    if reply["folder"].is_string(){app.exit(0);}
    Ok(reply)
}
