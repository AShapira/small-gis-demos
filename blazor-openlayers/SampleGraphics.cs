namespace BlazorOpenLayers;

// GeoJSON positions are [longitude, latitude]. OpenLayers projects them for display.
public static class SampleGraphics
{
    public static object Create() => new
    {
        type = "FeatureCollection",
        features = new object[]
        {
            Feature("Denver", "Point", new double[] { -104.99, 39.74 }),
            Feature("Chicago", "Point", new double[] { -87.63, 41.88 }),
            Feature("Western route", "LineString", new double[][] { [-122.42, 37.77], [-115.14, 36.17], [-112.07, 33.45] }),
            Feature("Eastern route", "LineString", new double[][] { [-84.39, 33.75], [-80.84, 35.23], [-77.04, 38.91] }),
            Feature("Northern area", "Polygon", new double[][][] { [[-112, 44], [-105, 44], [-105, 48], [-112, 48], [-112, 44]] }),
            Feature("Southern area", "Polygon", new double[][][] { [[-103, 30], [-96, 30], [-96, 35], [-103, 35], [-103, 30]] })
        }
    };

    private static object Feature(string name, string type, object coordinates) => new
    {
        type = "Feature",
        properties = new { name },
        geometry = new { type, coordinates }
    };
}
