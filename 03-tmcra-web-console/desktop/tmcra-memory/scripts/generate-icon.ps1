[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$SourcePath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "build\icon.ico"
}
if (-not $SourcePath) {
    $SourcePath = Join-Path $projectRoot "..\..\public\brand\tmcra-mark.png"
}

$output = [System.IO.Path]::GetFullPath($OutputPath)
$sourceFile = [System.IO.Path]::GetFullPath($SourcePath)
if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) {
    throw "The official TMCRA mark is missing: $sourceFile"
}

$directory = Split-Path -Parent $output
New-Item -ItemType Directory -Force -Path $directory | Out-Null

function Get-AlphaBounds([System.Drawing.Bitmap]$Image) {
    $rect = New-Object System.Drawing.Rectangle 0, 0, $Image.Width, $Image.Height
    $data = $Image.LockBits(
        $rect,
        [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    try {
        $stride = [Math]::Abs($data.Stride)
        $bytes = New-Object byte[] ($stride * $Image.Height)
        [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $bytes, 0, $bytes.Length)
        $minX = $Image.Width
        $minY = $Image.Height
        $maxX = -1
        $maxY = -1

        for ($y = 0; $y -lt $Image.Height; $y += 2) {
            $row = if ($data.Stride -lt 0) { ($Image.Height - 1 - $y) * $stride } else { $y * $stride }
            for ($x = 0; $x -lt $Image.Width; $x += 2) {
                if ($bytes[$row + ($x * 4) + 3] -gt 8) {
                    if ($x -lt $minX) { $minX = $x }
                    if ($x -gt $maxX) { $maxX = $x }
                    if ($y -lt $minY) { $minY = $y }
                    if ($y -gt $maxY) { $maxY = $y }
                }
            }
        }

        if ($maxX -lt 0 -or $maxY -lt 0) {
            throw "The official TMCRA mark contains no visible pixels."
        }

        $padding = [Math]::Max(4, [Math]::Round([Math]::Max($maxX - $minX, $maxY - $minY) * 0.02))
        $left = [Math]::Max(0, $minX - $padding)
        $top = [Math]::Max(0, $minY - $padding)
        $right = [Math]::Min($Image.Width - 1, $maxX + $padding)
        $bottom = [Math]::Min($Image.Height - 1, $maxY + $padding)
        return New-Object System.Drawing.Rectangle $left, $top, ($right - $left + 1), ($bottom - $top + 1)
    }
    finally {
        $Image.UnlockBits($data)
    }
}

function New-RoundedRectanglePath([System.Drawing.RectangleF]$Rect, [float]$Radius) {
    $diameter = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($Rect.Left, $Rect.Top, $diameter, $diameter, 180, 90)
    $path.AddArc($Rect.Right - $diameter, $Rect.Top, $diameter, $diameter, 270, 90)
    $path.AddArc($Rect.Right - $diameter, $Rect.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($Rect.Left, $Rect.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

$source = [System.Drawing.Bitmap]::FromFile($sourceFile)
$bitmap = New-Object System.Drawing.Bitmap 256, 256, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$background = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 248, 249, 250))
$border = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 216, 219, 222)), 2
$backgroundPath = New-RoundedRectanglePath (New-Object System.Drawing.RectangleF 8, 8, 240, 240) 42

try {
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.FillPath($background, $backgroundPath)
    $graphics.DrawPath($border, $backgroundPath)

    $sourceBounds = Get-AlphaBounds $source
    $availableWidth = 192.0
    $availableHeight = 202.0
    $scale = [Math]::Min($availableWidth / $sourceBounds.Width, $availableHeight / $sourceBounds.Height)
    $targetWidth = [float]($sourceBounds.Width * $scale)
    $targetHeight = [float]($sourceBounds.Height * $scale)
    $target = New-Object System.Drawing.RectangleF ((256 - $targetWidth) / 2), ((256 - $targetHeight) / 2), $targetWidth, $targetHeight
    $graphics.DrawImage($source, $target, $sourceBounds, [System.Drawing.GraphicsUnit]::Pixel)

    $pngPath = [System.IO.Path]::ChangeExtension($output, ".png")
    $pngStream = New-Object System.IO.MemoryStream
    try {
        $bitmap.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
        $bitmap.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $pngStream.ToArray()
    }
    finally {
        $pngStream.Dispose()
    }

    $file = [System.IO.File]::Open($output, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    $writer = New-Object System.IO.BinaryWriter $file
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]1)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$pngBytes.Length)
        $writer.Write([uint32]22)
        $writer.Write($pngBytes)
    }
    finally {
        $writer.Dispose()
        $file.Dispose()
    }
}
finally {
    $backgroundPath.Dispose()
    $border.Dispose()
    $background.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
    $source.Dispose()
}

Get-Item -LiteralPath $output | Select-Object FullName, Length
