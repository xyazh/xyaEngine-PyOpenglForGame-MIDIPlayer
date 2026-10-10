#version 330 core

const int POS = 1;
const int COL = 2;
const int TEX = 4;


in vec4 fragColor;
in vec2 fragTexCoord;
flat in int fragFormatType;

uniform vec4 data_color;
uniform sampler2D textureSampler;

out vec4 FragColor;


void main()
{
    vec4 color = data_color;
    if ((fragFormatType & TEX) != 0) {
        vec4 texColor = texture(textureSampler, fragTexCoord);
        if ((texColor.r == 0.0) && (texColor.g == 0.0) && (texColor.b == 0.0) && (texColor.a == 0.0)) {
            color = vec4(0.0, 1.0, 0.0, 1.0);
        }
    }
    FragColor = color;
}
