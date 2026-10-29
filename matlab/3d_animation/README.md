# 3-D airframe models

The models `pprz_replay` draws, in `3d_models/` (Nederdrone5, RotatingWingV3, Cyclone2).

To change or add a model:

1. Split the airframe into STL parts: the rigid body and each moving surface or rotor (for example with MeshLab).
2. Write a `convert_model.m` like the ones in `import_stl_model/<airframe>/`. It lists the parts, colours,
   offset, rotation and the actuator that moves each surface, and calls `convert3Dmodel` (which reads the
   STL files with `stlTools/`).
3. Run it from its folder; it writes `3d_models/<airframe>.mat`. Check the result with `plot3Dmodel`.

Based on [aircraft_3d_animation](https://github.com/Ro3code/aircraft_3d_animation) (GPL-3.0, see `LICENSE`).
