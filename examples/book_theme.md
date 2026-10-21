# 从变化中寻找规律

曲线的形状和变化速度，共同描述了一个函数。**导数描述局部的变化速度**，积分则把这些微小变化累积起来，连接起局部与整体。

## 一、用导数刻画变化

以二次函数 $f(x)=x^2$ 为例，它在 $x=1$ 处的斜率是 $2$。沿着曲线向右移动，切线逐渐变陡；在原点附近，变化则更平缓。

$$
f'(x)=\lim_{h\to 0}\frac{f(x+h)-f(x)}{h}=2x
$$

> 公式给出精确的关系，图形帮助我们看见关系。把两者放在一起，才能更容易理解抽象的数学概念。

\begin{tikzpicture}
\begin{axis}[width=11cm,height=5.5cm,axis lines=middle,
xmin=-1.5,xmax=2.1,ymin=-0.8,ymax=4.2,
xlabel={$x$},ylabel={$y$},grid=major,
grid style={gray!15},tick label style={font=\small},
legend style={draw=none,fill=none,at={(0.03,0.97)},anchor=north west}]
\addplot[domain=-1.5:2,samples=60,very thick,color={rgb,255:red,27;green,54;blue,93}] {x^2};
\addlegendentry{$f(x)=x^2$}
\addplot[domain=-0.2:2,samples=2,dashed,thick,gray] {2*x-1};
\addlegendentry{$y=2x-1$}
\addplot[only marks,mark=*,color={rgb,255:red,27;green,54;blue,93}] coordinates {(1,1)};
\end{axis}
\end{tikzpicture}

## 二、把表达与含义对应起来

| 表达 | 数学含义 |
| --- | --- |
| $f(x)$ | 自变量与函数值的对应关系 |
| $f'(x)$ | 某一点附近的变化速度 |
| $\int_a^b f(x)\,dx$ | 区间内带符号的累积量 |

### 用一段代码验证

中心差分用两侧的函数值估计导数。当步长足够小时，计算结果应接近解析值。

```python
def derivative(f, x, h=1e-5):
    return (f(x + h) - f(x - h)) / (2 * h)

print(derivative(lambda x: x**2, 1))  # 约等于 2
```
