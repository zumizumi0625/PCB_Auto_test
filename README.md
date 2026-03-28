# PCB Auto Test — KiCad CI/CD Pipeline

PCB 設計の自動テスト・CI/CD パイプライン。PR や push 時に回路図・基板のルールチェック、SPICE シミュレーション、製造ファイル生成を自動実行する。

## 機能

### 回路図・基板チェック（KiBot）
- **DRC** (Design Rule Check) — 基板の配線ルール違反を検出
- **ERC** (Electrical Rules Check) — 回路図の電気的ルール違反を検出
- **BOM 生成** — 部品表を HTML/CSV で自動生成
- **Gerber 生成** — 製造用ガーバーファイルを自動生成
- **3D レンダリング** — 基板の 3D 画像を生成
- **PDF 出力** — 回路図・基板レイアウトの PDF を生成

### SPICE シミュレーション（ngspice）
- **バッチ実行** — `.spice` ファイルを自動でシミュレーション
- **Pass/Fail 判定** — シミュレーション結果に対するアサーション
- **CI 統合** — テスト失敗でビルドを止める

### PR レビュー支援
- PR に DRC/ERC 結果をコメントとして自動投稿

## アーキテクチャ

```
┌─────────────┐     ┌──────────────────────────┐
│  Developer   │────▶│    GitHub Repository     │
│  (push/PR)   │     │                          │
└─────────────┘     └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │     GitHub Actions        │
                    │                           │
                    │  ┌─────────────────────┐  │
                    │  │  KiBot (Docker)     │  │
                    │  │  - DRC / ERC        │  │
                    │  │  - Gerber / BOM     │  │
                    │  │  - PDF / 3D Render  │  │
                    │  └─────────────────────┘  │
                    │                           │
                    │  ┌─────────────────────┐  │
                    │  │  ngspice            │  │
                    │  │  - Circuit sim      │  │
                    │  │  - Pass/Fail check  │  │
                    │  └─────────────────────┘  │
                    │                           │
                    │  ┌─────────────────────┐  │
                    │  │  PR Summary Bot     │  │
                    │  │  - Comment results  │  │
                    │  └─────────────────────┘  │
                    └───────────────────────────┘
```

## ディレクトリ構成

```
PCB_Auto_test/
├── .github/workflows/
│   └── pcb-ci.yml          # GitHub Actions ワークフロー
├── .kibot.yml               # KiBot 設定（DRC/ERC/出力定義）
├── hardware/
│   └── example/             # テスト用 KiCad プロジェクト
│       ├── batteryPack.*    # バッテリーパック基板
│       └── kibom-test-marked.*  # BOM テスト基板
├── simulation/
│   ├── run_simulations.py   # シミュレーションテストランナー
│   ├── example_rc_filter.spice       # RC フィルタテスト
│   └── example_voltage_divider.spice # 分圧回路テスト
└── docs/
```

## セットアップ

### 自分のプロジェクトで使う場合

1. このリポジトリの以下のファイルを自分のリポジトリにコピー:
   - `.github/workflows/pcb-ci.yml`
   - `.kibot.yml`
   - `simulation/run_simulations.py`

2. `.kibot.yml` のパスを自分のプロジェクト構成に合わせて修正

3. GitHub Actions のワークフローで `matrix.project` を自分の基板に合わせて修正:
   ```yaml
   matrix:
     project:
       - name: my-board
         board: hardware/my-board.kicad_pcb
         schema: hardware/my-board.kicad_sch
   ```

4. SPICE テストを追加する場合は `simulation/` に `.spice` ファイルを配置

### ローカルで動かす場合

```bash
# KiBot (Docker)
docker run --rm -v $(pwd):/workdir ghcr.io/inti-cmnb/kicad_auto_test:latest \
  kibot -c .kibot.yml -b hardware/example/batteryPack.kicad_pcb \
  -e hardware/example/batteryPack.kicad_sch

# ngspice
sudo apt install ngspice   # Ubuntu/Debian
sudo pacman -S ngspice     # Arch
python3 simulation/run_simulations.py
```

## CI パイプラインの動作

### トリガー
- `hardware/**`, `simulation/**`, `.kibot.yml` の変更時
- `main` ブランチへの push
- Pull Request

### 実行内容

| Job | 内容 | 失敗条件 |
|-----|------|----------|
| `kicad-checks` | DRC/ERC + 成果物生成 | DRC/ERC 違反 |
| `spice-simulation` | SPICE シミュレーション | テスト失敗 |
| `pr-summary` | PR にコメント投稿 | （失敗しない） |

### 成果物（Artifacts）
ビルド成功時、以下が GitHub Actions の Artifacts としてダウンロード可能:
- Gerber ファイル（製造用）
- BOM（HTML/CSV）
- 回路図・基板レイアウト PDF
- 3D レンダリング画像
- DRC/ERC レポート（JSON）

## SPICE テストの書き方

`simulation/` ディレクトリに `.spice` ファイルを配置するだけで自動テスト対象になる。

### テストの Pass/Fail 判定

`.control` ブロック内で `simulation_results.txt` に結果を書き出す:

```spice
.control
  run

  * 計算・測定
  let expected = 2.5
  let actual = v(out)
  let error = abs(actual - expected)

  * 結果ファイルに書き出し
  echo "RESULT:actual=$&actual" > simulation_results.txt
  echo "RESULT:expected=$&expected" >> simulation_results.txt

  * Pass/Fail 判定
  if error > 0.1
    echo "STATUS:FAIL" >> simulation_results.txt
  else
    echo "STATUS:PASS" >> simulation_results.txt
  end

  quit
.endc
```

## 使用ツール

| ツール | 用途 | ライセンス |
|--------|------|-----------|
| [KiBot](https://github.com/INTI-CMNB/KiBot) | KiCad 自動化 | GPL-3.0 |
| [KiCad](https://www.kicad.org/) | EDA ツール | GPL-3.0 |
| [ngspice](https://ngspice.sourceforge.io/) | SPICE シミュレータ | BSD |
| [KiDiff](https://github.com/INTI-CMNB/KiDiff) | 基板差分表示 | GPL-3.0 |

## テストデータのライセンス

`hardware/example/` のテストデータは [KiBot](https://github.com/INTI-CMNB/KiBot) のテストスイートから取得（GPL-3.0）。
