import unittest

import numpy as np
from PIL import Image

try:
    from affine_probe import affine, expected, metrics, pixel_coordinates, world
except ImportError:
    from rasterbench.affine_probe import affine, expected, metrics, pixel_coordinates, world


class AffineProbeTests(unittest.TestCase):
    def test_inverse_rotated_sheared_anisotropic_coordinates(self):
        for angle in (0, 15, 30, 45, 75, -30):
            gt = affine(angle, shear=True, scales=(2, .3))
            for col, row in ((0, 0), (.5, .5), (4095.5, 4095.5), (-20, 87)):
                ci, ri = pixel_coordinates(gt, *world(gt, col, row))
                self.assertAlmostEqual(float(ci), col, places=7)
                self.assertAlmostEqual(float(ri), row, places=7)

    def test_pixel_centres_north_up(self):
        source = np.array([[32, 64], [128, 192]], dtype=np.uint8)
        ref, valid = expected(source, [0, 1, 0, 2, 0, -1], [0, 0, 2, 2], 2, 2)
        np.testing.assert_array_equal(source, ref)
        self.assertTrue(valid.all())

    def test_rotated_footprint_not_bounding_rectangle(self):
        source = np.full((2, 2), 100, dtype=np.uint8)
        ref, valid = expected(source, [0, 1, 1, 2, 1, -1], [0, 0, 4, 4], 8, 8)
        self.assertTrue(valid.any())
        self.assertFalse(valid.all())
        result, holes = metrics(Image.fromarray(ref), ref, valid)
        self.assertEqual(result['missing_pixels'], 0)
        self.assertFalse(holes.any())

    def test_single_pixel_gap_is_not_hidden_by_footprint_tolerance(self):
        ref = np.full((4, 4), 100, dtype=np.uint8)
        valid = np.ones((4, 4), dtype=bool)
        image = ref.copy()
        image[0, 0] = 255
        result, holes = metrics(Image.fromarray(image), ref, valid)
        self.assertEqual(result['missing_pixels'], 1)
        self.assertTrue(holes[0, 0])

    def test_transparency_counts_as_missing_only_inside_footprint(self):
        ref = np.full((2, 2), 100, dtype=np.uint8)
        a = np.full((2, 2, 4), 100, dtype=np.uint8)
        a[:, :, 3] = 255
        a[0, 0, 3] = a[1, 1, 3] = 0
        valid = np.array([[False, True], [True, True]])
        result, _ = metrics(Image.fromarray(a), ref, valid)
        self.assertEqual(result['missing_pixels'], 1)


if __name__ == '__main__':
    unittest.main()
