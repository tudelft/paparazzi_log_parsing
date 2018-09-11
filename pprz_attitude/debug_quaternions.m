


t = 1:100;

len = length(t);

phi = ones(size(t)) .* 25 * pi / 180;
theta = ones(size(t)) .* 10 * pi / 180;
psi = t .* pi / 180;


for i=1:len
    
    q = quat_of_eulers([phi(i) theta(i) psi(i)]);
    
end




figure(1)
subplot(3,1,1)
hold off
plot(phi)
grid on

