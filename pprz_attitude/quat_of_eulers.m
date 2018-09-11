function [q] = quat_of_eulers(e)

phi2   = e(:, 1)    / 2;
theta2 = e(:, 2)    / 2;
psi2   = e(:, 3)    / 2;

s_phi2 = sin(phi2);
c_phi2 = cos(phi2);
s_theta2 = sin(theta2);
c_theta2 = cos(theta2);
s_psi2 = sin(psi2);
c_psi2 = cos(psi2);

c_th_c_ps = c_theta2 .* c_psi2;
c_th_s_ps = c_theta2 .* s_psi2;
s_th_s_ps = s_theta2 .* s_psi2;
s_th_c_ps = s_theta2 .* c_psi2;

q(:, 1) = c_phi2 .* c_th_c_ps + s_phi2 .* s_th_s_ps;
q(:, 2) = -c_phi2 .* s_th_s_ps + s_phi2 .* c_th_c_ps;
q(:, 3) = c_phi2 .* s_th_c_ps + s_phi2 .* c_th_s_ps;
q(:, 4) = c_phi2 .* c_th_s_ps + -s_phi2 .* s_th_c_ps;


end