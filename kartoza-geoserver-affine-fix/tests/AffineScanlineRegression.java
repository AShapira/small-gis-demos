package org.eclipse.imagen.media.affine;

import java.awt.geom.AffineTransform;
import java.awt.image.BufferedImage;
import org.eclipse.imagen.InterpolationNearest;
import org.eclipse.imagen.media.range.Range;

/** Checks half-open source bounds in the actual ImageN implementation. */
public class AffineScanlineRegression {
    public static void main(String[] args) {
        AffineNearestOpImage image = new AffineNearestOpImage(
                new BufferedImage(2, 2, BufferedImage.TYPE_BYTE_GRAY), null, null, null,
                new AffineTransform(), new InterpolationNearest(), new double[] {0}, true, false, null);
        check(image.performScanlineClipping(0, 0, 2, 2, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0), 0, 2);
        image.incx = -1;
        image.ifracdx = 0;
        check(image.performScanlineClipping(0, 0, 2, 2, 2, 0, 0, 0, 0, 4, 0, 0, 0, 0), 1, 3);
        image.incx = 1;
        image.incy = 1;
        image.ifracdy = 0;
        check(image.performScanlineClipping(0, 0, 2, 2, -3, 0, 0, 0, 0, 8, 0, 0, 0, 0), 3, 3);
        image.dispose();
        System.out.println("PASS: forward, reverse and empty scanline clipping");
    }

    private static void check(Range r, int min, int max) {
        if (r.getMin().intValue() != min || r.getMax().intValue() != max) {
            throw new AssertionError("Expected [" + min + "," + max + ") but got " + r);
        }
    }
}
