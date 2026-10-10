import glm

class Vec3(glm.vec3):
    pass


ZERO_V = Vec3(0, 0, 0)
ONE_VX3 = Vec3(1, 0, 0)
ONE_VY3 = Vec3(0, 1, 0)
ONE_VZ3 = Vec3(0, 0, 1)
ONE_VXP3 = Vec3(-1, 0, 0)
ONE_VYP3 = Vec3(0, -1, 0)
ONE_VZP3 = Vec3(0, 0, -1)