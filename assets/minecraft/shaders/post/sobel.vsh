#version 150

#moj_import <minecraft:projection.glsl>

in vec4 Position;

layout(std140) uniform SamplerInfo {
    vec2 OutSize;
    vec2 InSize;
};

out vec2 texCoord;
out vec2 oneTexel;

void main(){
    vec4 outPos = ProjMat * vec4(Position.xy * OutSize, 0.0, 1.0);
    gl_Position = vec4(outPos.xy, 0.2, 1.0);

    oneTexel = 1.0 / InSize;

    texCoord = Position.xy;
}
