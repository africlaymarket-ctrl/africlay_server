from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from authapp.permissions import IsAuthenticated
from product_management.models import Product

from .models import Cart, CartItem
from .serializers import CartSerializer, CartItemSerializer


class CartDetailView(generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return cart

    def get(self, request, *args, **kwargs):
        cart = self.get_object()
        serializer = self.get_serializer(cart)
        return Response(serializer.data)


class CartItemListCreateView(generics.ListCreateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return CartItem.objects.filter(cart=cart).select_related('product')

    def create(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        product_id = request.data.get('product')
        
        try:
            quantity = int(request.data.get('quantity', 1))
        except (ValueError, TypeError):
            raise ValidationError({'quantity': 'Quantity must be an integer.'})

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise ValidationError({'product': 'Product not found.'})

        if product.stock_quantity == 0:
            raise ValidationError({'product': 'Product is out of stock.'})

        if quantity > product.stock_quantity:
            raise ValidationError({'quantity': f'Only {product.stock_quantity} items available.'})

        existing_item = CartItem.objects.filter(cart=cart, product=product).first()
        if existing_item:
            new_quantity = existing_item.quantity + quantity
            if new_quantity > product.stock_quantity:
                raise ValidationError({'quantity': f'Total quantity {new_quantity} exceeds available stock {product.stock_quantity}.'})
            existing_item.quantity = new_quantity
            existing_item.save()
            serializer = self.get_serializer(existing_item)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(cart=cart, product=product)
            return Response(serializer.data, status=status.HTTP_201_CREATED)


class CartItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return CartItem.objects.filter(cart=cart).select_related('product')

    def perform_update(self, serializer):
        product = serializer.instance.product
        quantity = serializer.validated_data.get('quantity', serializer.instance.quantity)

        if quantity > product.stock_quantity:
            raise ValidationError({'quantity': f'Only {product.stock_quantity} items available.'})

        serializer.save()
