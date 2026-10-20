namespace BlazorOpenLayers;

public sealed class MapOptions
{
    public string Endpoint { get; set; } = "http://localhost:8080/geoserver/gwc/service/wmts";
    public string Layer { get; set; } = "topp:states";
    public string MatrixSet { get; set; } = "EPSG:900913";
    public string Format { get; set; } = "image/png";
    public double[] Center { get; set; } = [-98, 39];
    public double Zoom { get; set; } = 4;
}
