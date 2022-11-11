% Get the inital conditions for a discrete filter based on an intial value
function ic = get_ic(b,a,iv)

% Amount of channels to be filtered, determined based on the amount of ivs
n = size(iv,2);

% initial values for a and b coefficients
iva = ones(length(a)-1,1)*iv;
ivb = ones(length(b),1)*iv;

% pre allocate ic
ic = zeros(length(a)-1,n);

for k=1:n
    ic(:,k) = filtic(b,a,iva(:,k),ivb(:,k));
end

end