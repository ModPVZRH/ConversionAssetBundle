Shader "Spine/Skeleton"
{
    Properties
    {
        _Cutoff ("Shadow alpha cutoff", Range(0,1)) = 0.1
        [NoScaleOffset] _MainTex ("Main Texture", 2D) = "black" {}
        [Toggle(_STRAIGHT_ALPHA_INPUT)] _StraightAlphaInput ("Straight Alpha Texture", Float) = 0
        [HideInInspector] _StencilRef ("Stencil Reference", Float) = 1
        [Enum(UnityEngine.Rendering.CompareFunction)] [HideInInspector] _StencilComp ("Stencil Comparison", Float) = 8
        [HideInInspector] _OutlineWidth ("Outline Width", Range(0,8)) = 3
        [HideInInspector] _OutlineColor ("Outline Color", Color) = (1,1,0,1)
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "IgnoreProjector"="True" "RenderType"="Transparent" "PreviewType"="Plane" }
        Fog { Mode Off }
        Cull Off
        ZWrite Off
        Blend One OneMinusSrcAlpha
        Lighting Off

        Pass
        {
            CGPROGRAM
            #pragma shader_feature _ _STRAIGHT_ALPHA_INPUT
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                float4 color : COLOR;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                float4 color : COLOR;
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                o.color = v.color;
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                fixed4 tex = tex2D(_MainTex, i.uv);
                #ifdef _STRAIGHT_ALPHA_INPUT
                tex.rgb *= tex.a;
                #endif
                return tex * i.color;
            }
            ENDCG
        }
    }
    Fallback Off
}
