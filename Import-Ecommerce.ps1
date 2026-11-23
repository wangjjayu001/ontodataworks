param([switch]$ValidateOnly, [switch]$Resume)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$warehousePython = 'D:/agent语义取数文件夹/验证数据/ecommerce-data-modeling-main/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $warehousePython)) { throw '未找到包含 DuckDB 和 SQLGlot 的验证环境，请按电商迁移验收报告配置 Python 环境。' }
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $ValidateOnly) {
        $importArguments = @('scripts/import_ecommerce.py')
        if ($Resume) { $importArguments += '--resume' }
        & $warehousePython @importArguments
        if ($LASTEXITCODE -ne 0) { throw '迁移未完成；请检查输出，原始 DuckDB 不会被覆盖。' }
        & $warehousePython 'scripts/maintain_chinese_metadata.py'
        if ($LASTEXITCODE -ne 0) { throw '中文元数据初始化未完成，请检查输出。' }
    }
    & $warehousePython 'scripts/validate_ecommerce.py'
    if ($LASTEXITCODE -ne 0) { throw '验收未通过，请检查 evidence/ecommerce/sql_acceptance.json 及终端输出。' }
    & $warehousePython 'scripts/validate_chinese_metadata.py'
    if ($LASTEXITCODE -ne 0) { throw '中文元数据验收未通过，请检查 chinese_metadata_acceptance.json。' }
    & $warehousePython 'scripts/finalize_ecommerce.py'
    if ($LASTEXITCODE -ne 0) { throw '验收已通过，报告或备份生成未完成，请检查输出。' }
} finally { Pop-Location }
