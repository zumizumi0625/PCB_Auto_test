# PCB Auto Test

KiCad で設計した基板を GitHub に push するだけで、回路のチェックとシミュレーションが自動で走る CI/CD パイプライン。

## 何ができるか

**push / PR するだけで以下が全自動で実行される：**

1. **DRC（基板ルールチェック）** — 配線間隔、ビア径、シルク被りなどの違反を検出
2. **ERC（電気ルールチェック）** — 未接続ピン、電源ショート、ネット名重複を検出
3. **SPICE シミュレーション** — 回路の電気的動作を自動検証
4. **Discord 通知** — テスト結果をチャンネルに自動投稿
5. **製造ファイル生成** — Gerber、BOM、PDF、3D レンダー

テストが失敗したら波形画像も自動生成されて、Artifacts からダウンロードできる。

---

## クイックスタート（自分の基板で使う）

### Step 1: ファイルをコピー

自分の KiCad リポジトリに以下の 4 ファイルをコピーする：

```
自分のリポジトリ/
├── .github/workflows/pcb-ci.yml   ← ワークフロー
├── .kibot.yml                      ← KiBot設定
├── .drc-exclusions.json            ← DRC/ERC除外設定
├── simulation/run_simulations.py   ← テストランナー
├── tools/generate_spice_tests.py   ← 自動テスト生成
└── tools/check_drc_erc.py          ← DRC/ERC検証スクリプト
```

### Step 2: KiCad バージョンを設定

`.github/workflows/pcb-ci.yml` の冒頭で KiCad バージョンを指定する。2 箇所を揃えること：

```yaml
# ワークフロー冒頭の env
env:
  KICAD_IMAGE_TAG: ki9   # ki8, ki9 など

# KiBot アクションの uses タグ（env 変数は使えないので手動で合わせる）
uses: INTI-CMNB/KiBot@v2_k9  # ← KICAD_IMAGE_TAG に合わせて変更
```

| KiCad バージョン | `KICAD_IMAGE_TAG` | KiBot タグ |
|:---:|:---:|:---:|
| KiCad 8 | `ki8` | `INTI-CMNB/KiBot@v2_k8` |
| KiCad 9 | `ki9` | `INTI-CMNB/KiBot@v2_k9` |

> KiCad のバージョンが合っていないと `KiCad was unable to open this file` エラーになる。

### Step 3: ワークフローを修正

`.github/workflows/pcb-ci.yml` の `matrix.project` を自分の基板ファイルに合わせる：

```yaml
matrix:
  project:
    - name: my-board
      board: hardware/my-board.kicad_pcb
      schema: hardware/my-board.kicad_sch
```

スキーマティックの自動テスト生成のパスも合わせる：

```yaml
- name: Auto-generate SPICE tests from schematics
  run: |
    for sch in hardware/**/*.kicad_sch; do
      python3 tools/generate_spice_tests.py "$sch" || true
    done
```

### Step 4: Discord 通知を設定（任意）

1. Discord チャンネル → 設定 → 連携サービス → ウェブフック → URL をコピー
2. GitHub リポジトリ → Settings → Secrets → `DISCORD_WEBHOOK_URL` に URL を追加

### Step 5: push する

```bash
git add -A && git commit -m "CI追加" && git push
```

GitHub Actions が自動で走る。結果は Actions タブと Discord で確認できる。

---

## DRC/ERC 検証

CI は KiCad の DRC（Design Rule Check）と ERC（Electrical Rules Check）を実行し、違反の重大度に応じて pass/fail を判定する。

| 重大度 | CI への影響 | 例 |
|:---:|:---:|---|
| Error | CI 失敗 | 未接続ピン、コートヤード重なり、配線間隔不足 |
| Warning | レポートのみ | シルク被り、推奨外のビア径 |
| Excluded | 無視 | `.drc-exclusions.json` で除外した違反 |

### 意図的な未接続ピンの扱い

マイコンの未使用ピンなど、意図的に接続しないピンがある場合は **KiCad 上で No Connect（×）フラグを付ける** のが推奨。これにより ERC 違反にならない。

### 除外設定（`.drc-exclusions.json`）

No Connect フラグでは対応できない場合、除外設定ファイルで特定の違反を CI 判定から除外できる：

```json
{
  "excluded_types": ["silk_edge_clearance"],
  "excluded_descriptions": ["Pin unconnected.*NC"]
}
```

- `excluded_types`: 違反タイプ名で完全一致除外
- `excluded_descriptions`: 違反の説明文を正規表現でマッチして除外

---

## 自動テスト生成

KiCad のスキーマティック（`.kicad_sch`）を解析して SPICE テストを自動生成する。

```bash
python3 tools/generate_spice_tests.py my-board.kicad_sch
# → simulation/auto_my-board.spice が生成される
```

**自動検出されるもの：**

| 検出対象 | テスト内容 |
|---------|-----------|
| 電源ネット（`+3V3`, `+5V` 等） | 電圧値チェック |
| 抵抗分圧回路 | 出力電圧が理論値 ±5% 以内か |
| LED + 電流制限抵抗 | 電流が定格内か |
| 全抵抗の合計 | 消費電力概算 |

CI では push のたびに全スキーマティックからテストを自動生成してから実行する。新しい基板を追加するだけでテストが増える。

---

## 手書きの SPICE テスト

特定の回路に対して詳細なテストを書きたい場合は、`simulation/` に `.spice` ファイルを置く。置くだけで自動的にテスト対象になる。

### テストの書き方

```spice
.title My Circuit Test

* --- 回路定義 ---
V1 in 0 DC 5
R1 in out 10k
R2 out 0 10k

.control
  op

  * 測定
  let vout = v(out)
  echo "Vout: $&vout V"

  * 結果書き出し（テストランナーがパースする）
  echo "RESULT:vout=$&vout" > simulation_results.txt

  * Pass/Fail 判定
  let pass = 1
  if $&vout < 2.4
    echo "FAIL: Vout too low"
    let pass = 0
  end
  if $&vout > 2.6
    echo "FAIL: Vout too high"
    let pass = 0
  end

  if $&pass > 0
    echo "STATUS:PASS" >> simulation_results.txt
  else
    echo "STATUS:FAIL" >> simulation_results.txt
  end

  quit
.endc

.end
```

**ルール：**
- `.control` ブロック内で `simulation_results.txt` に `STATUS:PASS` か `STATUS:FAIL` を書く
- `RESULT:key=value` 形式で測定値を記録するとレポートに表示される
- 最後に必ず `quit` を入れる

### 同梱テストの一覧

| ファイル | 内容 | 検証項目 |
|---------|------|---------|
| `example_rc_filter.spice` | RC ローパスフィルタ | カットオフ周波数 |
| `example_voltage_divider.spice` | 抵抗分圧 | DC 出力電圧 |
| `i2c_bus_integrity.spice` | I2C バス | 立ち上がり時間、VOL、VOH |
| `spi_signal_integrity.spice` | SPI バス | オーバーシュート、セットアップタイム |
| `fault_overvoltage.spice` | 過電圧保護 | TVS クランプ動作 |
| `montecarlo_reliability.spice` | 部品公差 | 50 回モンテカルロサンプリング |
| `board_integration.spice` | 基板間接続 | ケーブル越し電圧ドロップ、I2C 品質 |
| `radiation_set.spice` | 放射線 SET | 3 段階パルス注入での耐性 |
| `crystal_oscillator.spice` | 水晶発振 | 発振確認、周波数精度 |
| `periodic_reset.spice` | 定期リセット | パルス周期、パルス幅 |
| `power_budget.spice` | 消費電力 | サブシステム別電流、バッテリ寿命 |

---

## Discord 通知の内容

### テスト成功時
```
✅ PCB CI — All Passed
`abc1234` feat: 電源回路修正
✅ KiCad DRC/ERC: success
✅ SPICE Simulation: success
SPICE: 11/11 passed
  ✅ example_rc_filter.spice
  ✅ i2c_bus_integrity.spice
  ...
```

### テスト失敗時
```
❌ PCB CI — Failures Detected
`abc1234` fix: 抵抗値変更
❌ SPICE Simulation: failure
SPICE: 9/11 passed
  ❌ i2c_bus_integrity.spice
      rise_time_ns = 2.07e-06
      vol = 0.014
  ❌ montecarlo_reliability.spice
      vout_min = 2.15
```

失敗時は波形画像も GitHub Actions の Artifacts からダウンロード可能。

---

## ローカルでの実行

### ngspice（SPICE シミュレーション）

```bash
# インストール
sudo apt install ngspice       # Ubuntu/Debian
sudo pacman -S ngspice         # Arch
brew install ngspice           # macOS

# テスト実行
python3 simulation/run_simulations.py
```

### KiBot（DRC/ERC）

```bash
# Docker で実行（推奨）
docker run --rm -v $(pwd):/workdir ghcr.io/inti-cmnb/kicad_auto_test:latest \
  kibot -c .kibot.yml \
  -b hardware/example/batteryPack.kicad_pcb \
  -e hardware/example/batteryPack.kicad_sch

# pip で直接インストール
pip install kibot
kibot -c .kibot.yml -b my-board.kicad_pcb -e my-board.kicad_sch
```

---

## ディレクトリ構成

```
PCB_Auto_test/
├── .github/workflows/
│   └── pcb-ci.yml              # CI ワークフロー
├── .kibot.yml                   # KiBot 設定
├── hardware/
│   └── example/                 # テスト用 KiCad データ
├── simulation/
│   ├── run_simulations.py       # テストランナー
│   ├── example_*.spice          # 基本テスト
│   ├── i2c_bus_integrity.spice  # I2C テスト
│   ├── spi_signal_integrity.spice
│   ├── fault_overvoltage.spice  # 過電圧保護
│   ├── radiation_set.spice      # 放射線耐性
│   ├── crystal_oscillator.spice # 水晶発振
│   ├── periodic_reset.spice     # ウォッチドッグ
│   ├── board_integration.spice  # 基板間接続
│   ├── power_budget.spice       # 消費電力
│   ├── montecarlo_reliability.spice  # モンテカルロ
│   └── auto_*.spice             # 自動生成テスト
├── tools/
│   ├── generate_spice_tests.py  # ネットリスト→SPICE 変換
│   └── check_drc_erc.py         # DRC/ERC 検証スクリプト
├── .drc-exclusions.json           # DRC/ERC 除外設定
└── docs/
```

---

## CI パイプラインの全体像

```
push / PR
    │
    ├── KiCad DRC/ERC (KiBot + kicad-cli)
    │   ├── DRC レポート (JSON) → 重大度別に判定
    │   ├── ERC レポート (JSON) → 重大度別に判定
    │   ├── Error あり → CI 失敗
    │   ├── Gerber ファイル
    │   ├── BOM (HTML/CSV)
    │   └── PDF / 3D レンダー
    │
    ├── SPICE Simulation (ngspice)
    │   ├── 自動テスト生成 (.kicad_sch → .spice)
    │   ├── 全 .spice 実行
    │   ├── JSON レポート出力
    │   └── 失敗時: 波形画像生成
    │
    └── Discord 通知
        ├── 成功: サマリー
        └── 失敗: 測定値・エラー詳細
```

---

## 使用ツール

| ツール | 用途 |
|--------|------|
| [KiBot](https://github.com/INTI-CMNB/KiBot) | KiCad 自動化（DRC/ERC/出力生成） |
| [ngspice](https://ngspice.sourceforge.io/) | SPICE 回路シミュレータ |
| [KiCad](https://www.kicad.org/) | 回路設計 EDA |

## ライセンス

`hardware/example/` のテストデータは [KiBot](https://github.com/INTI-CMNB/KiBot) のテストスイートから取得（GPL-3.0）。
