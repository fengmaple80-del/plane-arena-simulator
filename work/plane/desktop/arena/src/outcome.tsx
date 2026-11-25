import { ChartNoAxesColumnIncreasing, CircleAlert, ImageDown, Trophy, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import type { GameResult, Report } from "./backend"
import { otherSide, winRate } from "./result-presentation"
import type { SavedResult } from "./backend"

function pick(players: [string, string], [low, high]: [number, number]) {
  return low > 0.5 ? `${players[0]}更可能胜` : high < 0.5 ? `${players[1]}更可能胜` : "暂不能区分"
}

function GameOutcome({ game }: { game: GameResult }) {
  const bounds = game.left_sample_bounds
  return <>
    <div className="versus-caption"><span>{game.players[0]}</span><span>{game.players[1]}</span></div>
    <div className="probabilities">
      <span className="side-left">{winRate(bounds)}</span><span className="side-right">{winRate(otherSide(bounds))}</span>
    </div>
    <div className="win-bar" aria-hidden="true" style={{ gridTemplateColumns: `${game.left_wins}fr ${game.unknown}fr ${game.right_wins}fr` }}>
      <i /><i /><i />
    </div>
    <div className="sample-tallies"><span><b>{game.left_wins}</b> 左胜</span><span><b>{game.unknown}</b> 未判</span><span><b>{game.right_wins}</b> 右胜</span></div>
    <p className="game-pick">{pick(game.players, game.left_sample_bounds)}</p>
    {Object.entries(game.unjudged_reasons ?? {}).map(([reason, n]) => (
      <p className="result-warning" key={reason}><CircleAlert aria-hidden="true" />{reason}（{n} 次）</p>
    ))}
  </>
}

export function Outcome({ report, viewed, showGame, status, onExport, onPreview }: {
  report: Report | null; viewed: number; showGame: (game: number) => void; status: string
  onExport?: () => void; onPreview?: () => void
}) {
  const game = report?.games.find(g => g.game === viewed)
  const match = report?.matches[0]
  return <aside className="outcome" data-testid="results" aria-label="胜率结果">
    <div className="result-section-title"><ChartNoAxesColumnIncreasing aria-hidden="true" /><h2>胜率结果</h2>
      {game && <span>{game.samples} 次</span>}
      <Button type="button" className="result-export" variant="outline" size="sm" onClick={onExport} disabled={!onExport}>
        <ImageDown aria-hidden="true" />生成结果图
      </Button>
    </div>
    {report?.scoring && <section className="score-panel" aria-label="位面得分">
      <h3>{report.scoring.whole ? "三局整场积分" : "单局贡献分"}</h3>
      <p>已结算 {report.scoring.completed} / {report.scoring.samples.length} 次</p>
      <p>左方均分 <b>{report.scoring.averages.left?.toFixed(1) ?? "—"}</b>　右方均分 <b>{report.scoring.averages.right?.toFixed(1) ?? "—"}</b></p>
      <small>{report.scoring.whole ? "含参与分和整场胜利奖励，最高 9198 分。" : "按存活、击败、剩余生命和回合计分。参与分与胜利奖励在整场结算。"}</small>
      <details><summary>查看每次模拟得分</summary><div className="score-scroll"><table><thead><tr><th>次数</th><th>左方</th><th>右方</th></tr></thead><tbody>{report.scoring.samples.map((sample,i)=><tr key={sample.seed} title={`种子 ${sample.seed}`}><td>{i+1}</td><td>{sample.left ?? "未结算"}</td><td>{sample.right ?? "未结算"}</td></tr>)}</tbody></table></div></details>
    </section>}
    {game ? <><div className="game-label">第 {game.game} 小局</div><GameOutcome game={game} /></>
      : <p className="result-empty">{report ? "本小局尚未计算" : status}</p>}
    {match && <div className="overall" data-testid="match-summary">
      <div className="overall-title"><Trophy aria-hidden="true" /><h3>整场</h3><span>三局两胜</span></div>
      <div className="overall-numbers"><b className="side-left">{winRate(match.left_sample_bounds)}</b>
        <b className="side-right">{winRate(otherSide(match.left_sample_bounds))}</b></div>
      <p className="game-pick">{pick(match.players, match.left_sample_bounds)}</p>
      <div className="game-result-list">
        {report.games.map(g => <button type="button" key={g.game} className="game-result-row" onClick={() => showGame(g.game)} aria-pressed={g.game === viewed}>
          <span>第 {g.game} 小局</span><b className="side-left">{winRate(g.left_sample_bounds)}</b>
          <b className="side-right">{winRate(otherSide(g.left_sample_bounds))}</b>
        </button>)}
      </div>
    </div>}
    {onPreview && <Button type="button" variant="link" size="sm" onClick={onPreview}>预览已保存的结果图</Button>}
  </aside>
}

export function ResultImagePreview({ image, open, onOpenChange }: {
  image: { path: string; url: string }; open: boolean; onOpenChange: (open: boolean) => void
}) {
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="result-image-dialog" showCloseButton={false}>
      <DialogHeader><DialogTitle>结果图预览</DialogTitle>
        <DialogDescription data-testid="result-image-path">{image.path}</DialogDescription>
      </DialogHeader>
      <DialogClose asChild><Button className="result-preview-close" type="button" variant="ghost" size="icon-sm" aria-label="关闭预览" title="关闭预览">
        <X aria-hidden="true" />
      </Button></DialogClose>
      <div className="result-image-scroll"><img src={image.url} alt="位面战模拟结果图" /></div>
    </DialogContent>
  </Dialog>
}

export function ResultHistory({ entries, busy, onToggle, onRename, onExport, exportLabel }: {
  entries: SavedResult[]; busy: boolean; onToggle: (id: number) => void
  onRename: (id: number, label: string) => void; onExport?: () => void; exportLabel?: string
}) {
  const count = entries.filter(entry => entry.selected).length
  return <section className="result-history" aria-label="多场模拟结果">
    <div className="result-section-title">
      <ChartNoAxesColumnIncreasing aria-hidden="true" /><h2>多场模拟结果</h2>
      <span>{onExport ? `已选 ${count} / ${entries.length}` : `${entries.length} 场`}</span>
      {onExport && <Button className="result-export" size="sm" variant="outline" onClick={onExport} disabled={busy || !count}>
        <ImageDown aria-hidden="true" />{exportLabel}
      </Button>}
    </div>
    {entries.length > 0 && <div className="history-list">{entries.map(entry => {
      const { games, matches } = entry.report
      const result = matches[0] ?? games[0]
      return <div className={`history-row${onExport ? "" : " history-row--view"}`} key={entry.id}>
        {onExport && <input type="checkbox" checked={entry.selected} disabled={busy} onChange={() => onToggle(entry.id)}
          aria-label={`纳入第 ${entry.id} 场：${entry.label}`} />}
        <input className="history-name" aria-label={`第 ${entry.id} 场名称`} title="对局名称"
          value={entry.label} maxLength={56} disabled={busy} onChange={event => onRename(entry.id, event.target.value)} />
        <div className="history-meta"><span>{matches.length ? "整场" : `第 ${games[0].game} 局`}</span>
          <span>{matches.length ? "每局 " : ""}{games[0].samples} 次</span></div>
        <b className="side-left">{winRate(result.left_sample_bounds)}</b>
        <b className="side-right">{winRate(otherSide(result.left_sample_bounds))}</b>
      </div>
    })}</div>}
  </section>
}
