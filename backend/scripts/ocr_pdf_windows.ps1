param([Parameter(Mandatory=$true)][string]$PdfPath,
      [Parameter(Mandatory=$true)][string]$PageNumbers)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null

function Wait-Result($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}
function Wait-Action($Operation) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
    } | Select-Object -First 1
    $task = $method.Invoke($null, @($Operation))
    [void]$task.GetAwaiter().GetResult()
}

$file = Wait-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($PdfPath)) ([Windows.Storage.StorageFile])
$document = Wait-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'Windows OCR language pack is unavailable. Install English OCR or supply reviewed PDF text.' }
$results = @()
foreach ($number in $PageNumbers.Split(',')) {
    $page = $document.GetPage([uint32]([int]$number - 1))
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $bitmap = $null
    try {
        $options = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
        $scale = [Math]::Min(3.0, 4000.0 / [Math]::Max($page.Size.Width, $page.Size.Height))
        $options.DestinationWidth = [uint32]($page.Size.Width * $scale)
        $options.DestinationHeight = [uint32]($page.Size.Height * $scale)
        Wait-Action ($page.RenderToStreamAsync($stream, $options))
        $stream.Seek(0)
        $decoder = Wait-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocr = Wait-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $text = ($ocr.Lines | ForEach-Object { $_.Text }) -join "`n"
        $results += @{page_number=[int]$number; text=$text}
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        $stream.Dispose()
        $page.Dispose()
    }
}
ConvertTo-Json -InputObject @($results) -Compress -Depth 4
