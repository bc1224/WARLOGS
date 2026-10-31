# Reads text from images with the OCR engine built into Windows.
# Must run under Windows PowerShell 5.1 (powershell.exe), which can call WinRT.
#   ocr.ps1 <image>   -> one JSON array of lines
#   ocr.ps1 -Serve    -> reads image paths from stdin, writes one JSON line each
param([string]$Path, [switch]$Serve)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(-1)
    $task.Result
}

# The game is in English: use an English recognizer when Windows has one. One for
# another language (Russian, say) reads English letters as look-alikes of its own.
$english = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
    Where-Object { $_.LanguageTag -like 'en*' } | Select-Object -First 1
$engine = if ($english) { [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($english) } else { $null }
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }

function Read-Text([string]$imagePath) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $bitmap = $null
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        # The engine refuses images larger than MaxImageDimension, so scale down when needed.
        $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        $scale = [Math]::Min(1.0, $max / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight))
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = [uint32]($decoder.PixelWidth * $scale)
        $transform.ScaledHeight = [uint32]($decoder.PixelHeight * $scale)
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    } finally {
        # a full screen picture is 8 MB outside .NET's heap, so the garbage collector
        # doesn't hurry to free it: let go of it right away
        if ($bitmap) { $bitmap.Dispose() }
        $stream.Dispose()
    }
    $lines = foreach ($line in $result.Lines) {
        $box = (@($line.Words) | Select-Object -First 1).BoundingRect
        $end = (@($line.Words) | Select-Object -Last 1).BoundingRect
        [pscustomobject]@{ text = $line.Text; x = [int]($box.X / $scale); y = [int]($box.Y / $scale)
                           w = [int](($end.X + $end.Width - $box.X) / $scale); h = [int]($box.Height / $scale) }
    }
    ConvertTo-Json -InputObject @($lines) -Compress
}

if ($Serve) {
    # game text can hold any character; send UTF-8 so Python can decode it
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
    $reads = 0
    while ($null -ne ($p = [Console]::In.ReadLine())) {
        try { $out = Read-Text $p } catch { $out = ConvertTo-Json @{ error = $_.Exception.Message } -Compress }
        [Console]::Out.WriteLine($out)
        [Console]::Out.Flush()
        if (++$reads % 25 -eq 0) { [GC]::Collect() }  # keeps a long match from growing the process
    }
} else {
    Read-Text $Path
}
